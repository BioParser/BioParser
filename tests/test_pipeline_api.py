import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx2
import pytest
from conftest import TEST_API_KEY
from fastapi.testclient import TestClient
from httpx2 import ASGITransport
from stubs import STUB_JSON, StubMinerUClient, StubVLLMService, fake_mineru, file_parse_reply

from bioparser.api import config
from bioparser.api.app import app
from bioparser.api.pipeline import PipelineError, run_pipeline
from bioparser.services.vllm.vllm import VLLMError

pytestmark = pytest.mark.usefixtures("extract_semaphore")

PDF = b"%PDF-1.4\n%%EOF\n"


def _unused_submit(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/submit",
        files={"file": ("paper.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 202

    body = response.json()
    assert isinstance(body, dict)
    return body


# --- backpressure -----------------------------------------------------------


class SlowMinerU(StubMinerUClient):
    async def parse(
        self,
        *,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        await asyncio.sleep(0.3)  # stands in for minutes of real parsing
        return self.payload


@pytest.mark.anyio
async def test_a_saturated_pipeline_returns_503_instead_of_queueing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare `async with semaphore` bounds the work but not the waiting, so
    clients sat on open connections with no way to learn the service was busy.
    """
    from bioparser.api import config

    monkeypatch.setattr(
        app.state,
        "mineru",
        SlowMinerU(),
        raising=False,
    )
    monkeypatch.setattr(
        app.state,
        "vllm",
        StubVLLMService(),
        raising=False,
    )
    monkeypatch.setattr(
        app.state,
        "extract_sem",
        asyncio.Semaphore(1),
        raising=False,
    )
    monkeypatch.setattr(
        config.get_api_settings(),
        "extract_queue_timeout_seconds",
        0.05,
    )

    async with httpx2.AsyncClient(
        transport=ASGITransport(app),
        base_url="http://t",
        headers={"X-API-Key": TEST_API_KEY},
    ) as http:

        async def one(index: int) -> int:
            response = await http.post(
                "/extract",
                files={"file": (f"{index}.pdf", PDF, "application/pdf")},
            )
            return response.status_code

        codes = await asyncio.gather(*(one(index) for index in range(4)))

    assert codes.count(200) == 1
    assert codes.count(503) == 3


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- upstream failures ---------------------------------------------------


class OffSchemaVLLM(StubVLLMService):
    async def generate_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        system_prompt: str,
        max_tokens: int,
    ) -> str:
        return '{"observations": [{"taxon": 1}]}'  # valid JSON, wrong shape


class UnparseableMinerU(StubMinerUClient):
    async def parse(
        self,
        *,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        from bioparser.services.mineru import MinerUError

        raise MinerUError("mineru-api returned unparseable middle_json")


class StaleVersionMinerU(StubMinerUClient):
    async def parse(
        self,
        *,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        return {**STUB_JSON, "metadata": {"producer": {"name": "mineru", "version": "9.9.9"}}}


class StructurallyWrongMinerU(StubMinerUClient):
    async def parse(
        self,
        *,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        return {**STUB_JSON, "pages": [{"blocks": []}]}


class HallucinatingVLLM(StubVLLMService):
    async def generate_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        system_prompt: str,
        max_tokens: int,
    ) -> str:
        return json.dumps(
            {
                "observations": [
                    {
                        "taxon": "Mus musculus",
                        "trait": "body_mass",
                        "value": 99.9,
                        "unit": "g",
                        "evidence_block_id": "no-such-block",
                        "quotation": "never appeared in the paper",
                    }
                ]
            }
        )


def test_extract_returns_502_for_upstream_misbehaviour(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app.state,
        "mineru",
        UnparseableMinerU(),
        raising=False,
    )
    monkeypatch.setattr(
        app.state,
        "vllm",
        StubVLLMService(),
        raising=False,
    )

    response = client.post(
        "/extract",
        files={"file": ("a.pdf", PDF, "application/pdf")},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Parsing failed"


@pytest.mark.parametrize(
    "mineru", [StaleVersionMinerU(), StructurallyWrongMinerU()], ids=["stale", "malformed"]
)
def test_extract_returns_502_when_the_parser_output_cannot_be_mapped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, mineru: StubMinerUClient
) -> None:
    monkeypatch.setattr(app.state, "mineru", mineru, raising=False)
    monkeypatch.setattr(app.state, "vllm", StubVLLMService(), raising=False)

    response = client.post("/extract", files={"file": ("a.pdf", PDF, "application/pdf")})

    assert response.status_code == 502
    assert response.json()["detail"] == "Parser output could not be mapped"


class MismatchedUnitVLLM(StubVLLMService):
    async def generate_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        system_prompt: str,
        max_tokens: int,
    ) -> str:
        match = re.search(r"\[([^\]]+)\]", prompt)
        return json.dumps(
            {
                "observations": [
                    {
                        "taxon": "Mus musculus",
                        "trait": "body_mass",
                        "value": 12.5,
                        "unit": "mm",  # a mass in millimetres
                        "evidence_block_id": match.group(1) if match else "x",
                        "quotation": "12.5 g",
                    }
                ]
            }
        )


def _extract(client: TestClient) -> dict[str, Any]:
    response = client.post("/extract", files={"file": ("a.pdf", PDF, "application/pdf")})
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return body


def test_a_grounded_observation_is_flagged_grounded(
    client: TestClient,
    stub_pipeline: tuple[StubMinerUClient, StubVLLMService],
) -> None:
    body = _extract(client)

    assert body["ungrounded_observations"] == 0
    assert body["observations"][0]["grounded"] is True
    assert body["observations"][0]["unit_ok"] is True
    assert body["observations"][0]["canonical_value"] == 12.5  # grams


def test_an_ungrounded_observation_is_returned_flagged_not_silently(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A quotation the paper does not contain must not look like evidence."""
    monkeypatch.setattr(app.state, "mineru", StubMinerUClient(), raising=False)
    monkeypatch.setattr(app.state, "vllm", HallucinatingVLLM(), raising=False)

    body = _extract(client)

    assert body["ungrounded_observations"] == 1
    assert body["observations"][0]["grounded"] is False


def test_a_unit_the_trait_cannot_take_is_flagged(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app.state, "mineru", StubMinerUClient(), raising=False)
    monkeypatch.setattr(app.state, "vllm", MismatchedUnitVLLM(), raising=False)

    body = _extract(client)

    assert body["unit_mismatched_observations"] == 1
    assert body["observations"][0]["unit_ok"] is False


def test_a_unit_outside_the_closed_set_fails_the_extraction(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guided decoding should prevent this; pydantic is the backstop."""

    class FreeTextUnitVLLM(StubVLLMService):
        async def generate_json(
            self,
            prompt: str,
            *,
            schema: dict[str, Any],
            system_prompt: str,
            max_tokens: int,
        ) -> str:
            return json.dumps(
                {
                    "observations": [
                        {
                            "taxon": "Mus musculus",
                            "trait": "body_mass",
                            "value": 12.5,
                            "unit": "grams",
                            "evidence_block_id": "x",
                            "quotation": "12.5 g",
                        }
                    ]
                }
            )

    monkeypatch.setattr(app.state, "mineru", StubMinerUClient(), raising=False)
    monkeypatch.setattr(app.state, "vllm", FreeTextUnitVLLM(), raising=False)

    response = client.post("/extract", files={"file": ("a.pdf", PDF, "application/pdf")})

    assert response.status_code == 502
    assert response.json()["detail"] == "Extraction did not match the schema"


class _UnreachableVLLM:
    async def generate_json(
        self, prompt: str, *, schema: Any, system_prompt: str, max_tokens: int
    ) -> str:
        raise VLLMError("vLLM request failed: APIConnectionError")


@pytest.mark.anyio
async def test_an_unreachable_vllm_becomes_a_pipeline_error() -> None:
    # vLLM is a separate container with a long startup
    with pytest.raises(PipelineError, match="Extraction backend unavailable"):
        await run_pipeline(
            mineru=StubMinerUClient(),
            vllm=_UnreachableVLLM(),
            checksum="0" * 64,
            content=PDF,
            char_budget=4000,
            max_tokens=256,
        )


def test_the_api_asks_mineru_for_the_configured_tier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BIOPARSER_MINERU_TIER", "standard")
    monkeypatch.setenv("BIOPARSER_VLLM_BASE_URL", "http://127.0.0.1:9/v1")  # nothing answers
    # The lifespan needs both. It only builds the Redis clients; nothing connects.
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://127.0.0.1:9/15")
    monkeypatch.setenv("BIOPARSER_ARTIFACT_STORAGE_PATH", str(tmp_path / "artifacts"))
    # The lifespan overwrites these; registering them lets monkeypatch restore them.
    for name in ("mineru", "vllm", "job_store", "parse_queue", "artifact_storage", "extract_sem"):
        monkeypatch.setattr(app.state, name, None, raising=False)
    with fake_mineru(file_parse_reply()) as (url, seen):
        monkeypatch.setenv("BIOPARSER_MINERU_BASE_URL", url)
        config.get_api_settings.cache_clear()  # read before the environment was set
        with TestClient(app) as client:
            pdf = ("a.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")
            response = client.post("/extract", files={"file": pdf})

    assert response.status_code == 502  # the extraction model is not there
    assert "tier=standard&" in seen[0]
