import asyncio
import contextlib
import dataclasses
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from conftest import TEST_API_KEY
from fastapi.testclient import TestClient
from httpx2 import ASGITransport
from stubs import (
    LOCATABLE_OBSERVATION,
    LOCATABLE_TEXT,
    InlinePipeline,
    StubParser,
    StubVLLMService,
    fake_mineru,
    file_parse_reply,
    stub_parser_artifact,
)

from bioparser import pipeline as pipeline_module
from bioparser.api import config
from bioparser.api.app import app
from bioparser.extract.models import ExtractionArtifact
from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME
from bioparser.parser.errors import (
    ParserProcessError,
    ParserTimeoutError,
    UnsupportedDocumentError,
)
from bioparser.parser_names import DEFAULT_PARSER_NAME
from bioparser.pipeline import PipelineError, PipelineOptions, run_pipeline
from bioparser.services.vllm.vllm import VLLMError

# bioparser.api re-exports the FastAPI instance as `app`, which shadows the
# submodule of the same name; import_module returns the module itself.
api_app_module = importlib.import_module("bioparser.api.app")

pytestmark = pytest.mark.usefixtures("extract_semaphore")

PDF = b"%PDF-1.4\n%%EOF\n"
FIXTURE_PDF = Path(__file__).parent / "parser" / "fixtures" / "plos-biology-3000248.pdf"
OPTIONS = PipelineOptions(
    parser=DEFAULT_PARSER_NAME,
    extractor=DEFAULT_EXTRACTOR_NAME,
    parser_timeout_s=60.0,
    mineru_base_url="http://127.0.0.1:9",
    mineru_tier="basic",
    char_budget=4000,
    max_tokens=256,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _extract(client: TestClient) -> dict[str, Any]:
    response = client.post("/extract", files={"file": ("a.pdf", PDF, "application/pdf")})
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return body


# --- the same pipeline as a queued job --------------------------------------


def test_extract_runs_the_parser_and_extractor_of_a_queued_job(
    client: TestClient, inline_pipeline: InlinePipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = config.get_api_settings()
    monkeypatch.setattr(settings, "mineru_tier", "standard")

    body = _extract(client)

    [(name, kwargs)] = inline_pipeline.parser_requests
    assert name == DEFAULT_PARSER_NAME  # what POST /api/extractions records on the job
    assert kwargs == {
        "timeout_s": settings.mineru_timeout_seconds,
        "mineru_base_url": settings.mineru_base_url,
        "mineru_tier": "standard",
    }
    assert inline_pipeline.parser.pdfs == [PDF]
    # The response is the artifact the extractor worker stores for the job
    assert ExtractionArtifact.model_validate(body).model_dump(mode="json") == body
    assert body["schema_version"] == "1"


def _citation(text: str, start: int) -> dict[str, Any]:
    evidence = [{"block_id": "stub-p1-b0000", "start": start, "end": start + len(text)}]
    return {"kind": "citation", "text": text, "evidence": evidence}


def test_a_located_observation_is_returned_with_its_ranges(
    client: TestClient, inline_pipeline: InlinePipeline
) -> None:
    inline_pipeline.parser = StubParser(stub_parser_artifact(LOCATABLE_TEXT))
    inline_pipeline.vllm = StubVLLMService([LOCATABLE_OBSERVATION])

    [observation] = _extract(client)["observations"]

    assert observation["trait"]["value"] == "body_mass"
    assert observation["verbatimScientificName"] == _citation("Mus musculus", 0)
    assert observation["verbatimTraitValue"] == _citation("12.5", 30)
    assert observation["verbatimTraitUnit"] == _citation("g", 35)


def test_a_quotation_the_paper_does_not_contain_is_dropped(
    client: TestClient, inline_pipeline: InlinePipeline
) -> None:
    """A quotation the paper does not contain must not look like evidence."""
    hallucinated = LOCATABLE_OBSERVATION | {
        "quotation": "Mus musculus weighed 99.9 g",
        "value": 99.9,
    }
    inline_pipeline.parser = StubParser(stub_parser_artifact(LOCATABLE_TEXT))
    inline_pipeline.vllm = StubVLLMService([hallucinated])

    assert _extract(client)["observations"] == []


# --- failures ----------------------------------------------------------------


@dataclasses.dataclass
class OtherExtractor:
    """Returns an artifact for another extractor, which the extractor worker refuses."""

    async def extract(self, artifact: Any) -> ExtractionArtifact:
        return ExtractionArtifact(extractor="other", extractor_version="1")


def test_an_artifact_the_extractor_worker_refuses_is_not_returned(
    client: TestClient, inline_pipeline: InlinePipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline_module, "get_extractor", lambda *_a, **_k: OtherExtractor())

    response = client.post("/extract", files={"file": ("a.pdf", PDF, "application/pdf")})

    assert response.status_code == 502
    assert response.json()["detail"] == "Extraction failed its checks"


class FreeTextUnitVLLM(StubVLLMService):
    async def generate_json(
        self, prompt: str, *, schema: dict[str, Any], system_prompt: str, max_tokens: int
    ) -> str:
        observation = {
            "taxon": "Mus musculus",
            "trait": "body_mass",
            "value": 12.5,
            "unit": "grams",  # guided decoding should prevent this; pydantic is the backstop
            "evidence_block_id": "x",
            "quotation": "12.5 g",
        }
        return json.dumps({"observations": [observation]})


class UnreachableVLLM(StubVLLMService):
    async def generate_json(
        self, prompt: str, *, schema: dict[str, Any], system_prompt: str, max_tokens: int
    ) -> str:
        raise VLLMError("vLLM request failed: APIConnectionError")  # a separate, slow container


@pytest.mark.parametrize(
    ("parser", "vllm", "detail"),
    [
        (StubParser(error=ParserProcessError("mineru-api unreachable")), None, "Parsing failed"),
        (StubParser(error=ParserTimeoutError("ran out")), None, "Parsing timed out"),
        (
            StubParser(error=UnsupportedDocumentError("no text")),
            None,
            "The PDF could not be parsed",
        ),
        (None, FreeTextUnitVLLM(), "Extraction did not match the schema"),
        (None, UnreachableVLLM(), "Extraction backend unavailable"),
    ],
    ids=["parser-down", "parser-timeout", "unreadable-pdf", "off-schema", "vllm-down"],
)
def test_a_failed_step_returns_502_naming_it(
    client: TestClient,
    inline_pipeline: InlinePipeline,
    parser: StubParser | None,
    vllm: StubVLLMService | None,
    detail: str,
) -> None:
    inline_pipeline.parser = parser or inline_pipeline.parser
    inline_pipeline.vllm = vllm or inline_pipeline.vllm

    response = client.post("/extract", files={"file": ("a.pdf", PDF, "application/pdf")})

    assert response.status_code == 502
    assert response.json()["detail"] == detail


def test_run_pipeline_names_an_unreachable_vllm(inline_pipeline: InlinePipeline) -> None:
    inline_pipeline.vllm = UnreachableVLLM()

    with pytest.raises(PipelineError, match="Extraction backend unavailable"):
        run_pipeline(PDF, OPTIONS)


def test_run_pipeline_asks_mineru_for_the_configured_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real MinerU parser against a mineru-api on loopback, for a job whose parser is mineru
    vllm = StubVLLMService()
    monkeypatch.setattr(pipeline_module, "VLLMService", lambda: vllm)
    with fake_mineru(file_parse_reply()) as (url, seen):
        options = dataclasses.replace(
            OPTIONS, parser="mineru", mineru_base_url=url, mineru_tier="standard"
        )
        run_pipeline(PDF, options)

    assert "tier=standard&" in seen[0]
    # The extractor read the MinerU parse
    assert "Adult body mass averaged 12.5 g" in vllm.prompts[0]


# --- backpressure and the event loop ----------------------------------------


@pytest.mark.anyio
async def test_a_saturated_pipeline_returns_503_instead_of_queueing(
    inline_pipeline: InlinePipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare `async with semaphore` bounds the work but not the waiting, so
    clients sat on open connections with no way to learn the service was busy.
    """
    inline_pipeline.parser = StubParser(delay_s=0.3)
    monkeypatch.setattr(app.state, "extract_sem", asyncio.Semaphore(1), raising=False)
    monkeypatch.setattr(config.get_api_settings(), "extract_queue_timeout_seconds", 0.05)

    async with httpx2.AsyncClient(
        transport=ASGITransport(app), base_url="http://t", headers={"X-API-Key": TEST_API_KEY}
    ) as http:

        async def one(index: int) -> int:
            response = await http.post(
                "/extract", files={"file": (f"{index}.pdf", PDF, "application/pdf")}
            )
            return response.status_code

        codes = await asyncio.gather(*(one(index) for index in range(4)))

    assert codes.count(200) == 1
    assert codes.count(503) == 3


@pytest.mark.anyio
async def test_the_event_loop_answers_while_a_pdf_is_parsed(
    inline_pipeline: InlinePipeline,
) -> None:
    inline_pipeline.parser = StubParser(delay_s=0.5)

    async with httpx2.AsyncClient(
        transport=ASGITransport(app), base_url="http://t", headers={"X-API-Key": TEST_API_KEY}
    ) as http:
        extraction = asyncio.create_task(
            http.post("/extract", files={"file": ("a.pdf", PDF, "application/pdf")})
        )
        await asyncio.sleep(0.05)  # the parse has started
        health = await asyncio.wait_for(http.get("/health"), timeout=0.2)
        assert not extraction.done()
        assert (await extraction).status_code == 200

    assert health.status_code == 200


@pytest.mark.anyio
async def test_a_client_that_goes_away_keeps_the_slot_until_the_run_ends(
    inline_pipeline: InlinePipeline,
) -> None:
    inline_pipeline.parser = StubParser(delay_s=0.3)
    sem = asyncio.Semaphore(1)
    fake_app = SimpleNamespace(
        state=SimpleNamespace(extract_sem=sem, pipeline_pool=ThreadPoolExecutor(max_workers=1))
    )

    run, _pool = await api_app_module._start_pipeline(fake_app, PDF, OPTIONS)

    async def request() -> None:  # what the route does with the run
        await asyncio.shield(run)

    waiter = asyncio.create_task(request())
    await asyncio.sleep(0.05)
    waiter.cancel()  # the request is gone ...
    with contextlib.suppress(asyncio.CancelledError):
        await waiter
    assert sem.locked()  # ... but the child still runs, so its slot stays taken
    await run
    await asyncio.sleep(0)  # the done callback
    assert not sem.locked()
    fake_app.state.pipeline_pool.shutdown()


# --- the real process pool ---------------------------------------------------


@pytest.mark.parametrize(
    ("available", "chosen"),
    [(["fork", "spawn", "forkserver"], "forkserver"), (["spawn"], "spawn")],
    ids=["posix", "windows"],
)
def test_the_pool_starts_its_processes_without_fork(
    monkeypatch: pytest.MonkeyPatch, available: list[str], chosen: str
) -> None:
    monkeypatch.setattr("multiprocessing.get_all_start_methods", lambda: available)

    assert api_app_module._pipeline_start_method() == chosen


def test_extract_runs_the_pipeline_in_a_child_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The lifespan's own pool: the real pdfplumber parse in a child, which reaches no vLLM."""
    monkeypatch.setenv("BIOPARSER_VLLM_BASE_URL", "http://127.0.0.1:9/v1")  # nothing answers
    # The lifespan opens the pool and the queue client; nothing connects yet.
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://127.0.0.1:9/15")
    monkeypatch.setenv("BIOPARSER_DATABASE_URL", "postgresql://bioparser@127.0.0.1:1/bioparser")
    monkeypatch.setenv("BIOPARSER_ARTIFACT_STORAGE_PATH", str(tmp_path / "artifacts"))
    # The lifespan overwrites these; registering them lets monkeypatch restore them.
    for name in ("vllm", "parse_queue", "artifact_storage", "extract_sem"):
        monkeypatch.setattr(app.state, name, None, raising=False)
    monkeypatch.setattr(app.state, "pipeline_pool", None, raising=False)
    config.get_api_settings.cache_clear()  # read before the environment was set

    with TestClient(app, headers={"X-API-Key": TEST_API_KEY}) as client:
        pdf = ("paper.pdf", FIXTURE_PDF.read_bytes(), "application/pdf")
        response = client.post("/extract", files={"file": pdf})

    assert response.status_code == 502
    assert response.json()["detail"] == "Extraction backend unavailable"
