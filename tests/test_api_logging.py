"""Logging in the API layer: vLLM model discovery at startup, and pipeline stages."""

import copy
import importlib
import itertools
import json
import logging
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from stubs import STUB_JSON, Handler, StubMinerUClient, StubVLLMService

from bioparser import logging_config
from bioparser.api import config
from bioparser.api import pipeline as pipeline_module
from bioparser.api.app import app
from bioparser.api.pipeline import PipelineError, run_pipeline
from bioparser.logging_config import JsonFormatter, setup_logging
from bioparser.services.mineru import MinerUClient, MinerUError
from bioparser.services.vllm import config as vllm_config
from bioparser.services.vllm.vllm import VLLMService

# bioparser.api re-exports the FastAPI instance as `app`, which shadows the
# submodule of the same name; import_module returns the module itself.
app_module = importlib.import_module("bioparser.api.app")

PDF = b"%PDF-1.4\n%%EOF\n"
CHECKSUM = "ab" * 32
API_KEY = "sk-canary-api-key-7f3a"
MODELS = {"object": "list", "data": [{"id": "stub-model"}]}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _rendered(records: list[logging.LogRecord]) -> str:
    return "\n".join(JsonFormatter().format(record) for record in records)


# --- startup: vLLM model discovery -----------------------------------------


class _ProbedVLLM:
    """What the lifespan needs from a VLLMService: get_model() and aclose()."""

    def __init__(self, outcome: str | Exception) -> None:
        self.outcome = outcome

    async def get_model(self) -> str:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def aclose(self) -> None:
        return None


def _app(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient whose lifespan-built clients are dropped again after the test."""
    for name in ("mineru", "vllm"):
        monkeypatch.setattr(app.state, name, None, raising=False)
    return TestClient(app)


def _start_app(monkeypatch: pytest.MonkeyPatch, outcome: str | Exception) -> TestClient:
    """TestClient: lifespan probes a vLLM that returns or raises [outcome]"""
    monkeypatch.setattr(app_module, "VLLMService", lambda: _ProbedVLLM(outcome))
    return _app(monkeypatch)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.levelno == logging.WARNING]


def _refused(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("connection refused", request=request)


def test_a_model_discovery_failure_emits_exactly_one_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    vllm_transport: Callable[[Handler], None],
) -> None:
    # The real VLLMService and discovery code: a stub would hide a second
    # warning logged inside get_model().
    vllm_transport(_refused)

    with _app(monkeypatch):
        pass

    [warning] = _warnings(caplog)
    assert warning.getMessage() == "vllm model discovery failed"
    assert (vars(warning)["error"], vars(warning)["cause"]) == ("VLLMError", "APIConnectionError")
    assert not warning.exc_info  # vLLM not up yet is expected: no traceback


def test_an_unexpected_discovery_error_keeps_its_traceback_and_startup_continues(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with _start_app(monkeypatch, RuntimeError("a bug")) as client:
        assert client.get("/health").status_code == 200

    [warning] = _warnings(caplog)
    assert vars(warning)["error"] == "RuntimeError"
    assert warning.exc_info is not None


def test_a_discovered_model_is_logged_with_the_base_url_redacted(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    configured = vllm_config.Settings(vllm_base_url="http://user:canary-pass@vllm:8000/v1")
    monkeypatch.setattr(app_module, "get_vllm_settings", lambda: configured)

    with _start_app(monkeypatch, "Qwen/Qwen3-0.6B"):
        pass

    [record] = [r for r in caplog.records if r.getMessage() == "vllm model discovered"]
    assert (vars(record)["model"], vars(record)["vllm_base_url"]) == (
        "Qwen/Qwen3-0.6B",
        "http://***@vllm:8000/v1",
    )
    assert "canary-pass" not in _rendered(caplog.records)


def test_the_redis_password_never_appears_in_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    password = "canary-redis-pass"
    monkeypatch.setenv("BIOPARSER_REDIS_URL", f"redis://:{password}@127.0.0.1:1/0")
    monkeypatch.setenv("BIOPARSER_LOG_LEVEL", "DEBUG")
    caplog.set_level(logging.DEBUG)

    with _start_app(monkeypatch, "stub-model") as client:
        assert client.get("/ready").status_code == 503  # port 1 refuses the connection

    assert password not in _rendered(caplog.records) + capsys.readouterr().err
    assert password not in repr(config.get_api_settings())


def test_a_malformed_redis_url_does_not_echo_its_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # app.py loads the settings at import, so this error reaches the container
    # log as a plain traceback, before any logging is configured.
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://:canary-redis-pass@redis:99999/0")

    with pytest.raises(ValidationError, match="redis_url") as failure:
        config.ApiSettings()

    assert "canary-redis-pass" not in str(failure.value)


def test_the_configured_api_key_never_appears_in_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    vllm_transport: Callable[[Handler], None],
) -> None:
    """Startup, a request, and a failure whose traceback holds a key-quoting error."""
    monkeypatch.setenv("BIOPARSER_VLLM_API_KEY", API_KEY)
    monkeypatch.setenv("BIOPARSER_LOG_LEVEL", "DEBUG")
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(app_module, "MinerUClient", lambda *_args: StubMinerUClient())
    sent: list[str] = []

    def rejects_the_key(request: httpx2.Request) -> httpx2.Response:
        sent.append(request.headers["authorization"])
        if request.url.path.endswith("/models"):
            return httpx2.Response(200, json=MODELS)
        # Some gateways quote the rejected credential back in the error body
        quoted = request.headers["authorization"]
        return httpx2.Response(401, json={"error": {"message": f"Incorrect API key: {quoted}"}})

    vllm_transport(rejects_the_key)

    with _app(monkeypatch) as client:
        response = client.post("/extract", files={"file": ("paper.pdf", PDF, "application/pdf")})

    assert response.status_code == 502
    assert sent == [f"Bearer {API_KEY}"] * 2  # discovery at startup, then the completion
    logs = _rendered(caplog.records) + capsys.readouterr().err
    assert '"exc_info"' in logs  # the traceback path really ran
    assert API_KEY not in logs


# --- pipeline stages -------------------------------------------------------


@pytest.fixture
def pipeline_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> pytest.LogCaptureFixture:
    """Capture the pipeline's records, with a clock that makes every stage take 250 ms."""
    caplog.set_level(logging.INFO, logger=pipeline_module.__name__)
    monkeypatch.setattr(logging_config, "perf_counter", itertools.count(100.0, 0.25).__next__)
    return caplog


def _stage_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == pipeline_module.__name__]


async def _run(mineru: object, vllm: object) -> dict[str, Any]:
    return await run_pipeline(
        mineru=mineru,
        vllm=vllm,
        checksum=CHECKSUM,
        content=PDF,
        char_budget=4000,
        max_tokens=256,
    )


@pytest.mark.anyio
async def test_each_stage_logs_its_duration_and_results(
    pipeline_log: pytest.LogCaptureFixture,
) -> None:
    await _run(StubMinerUClient(), StubVLLMService())

    records = _stage_records(pipeline_log)
    assert [(vars(r)["stage"], vars(r)["outcome"], vars(r)["duration_ms"]) for r in records] == [
        ("parse", "ok", 250.0),
        ("map", "ok", 250.0),
        ("extract", "ok", 250.0),
    ]
    assert {r.levelno for r in records} == {logging.INFO}
    _parse, mapped, extracted = map(vars, records)
    assert mapped["pages"] == 1
    assert (extracted["observations"], extracted["ungrounded_observations"]) == (1, 0)


class _UnreachableMinerU(StubMinerUClient):
    async def parse(self, *, filename: str, content: bytes) -> dict[str, Any]:
        raise MinerUError("mineru-api unreachable: ConnectError") from httpx2.ConnectError("")


@pytest.mark.anyio
async def test_a_pipeline_failure_names_the_stage_and_the_cause(
    pipeline_log: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(PipelineError, match="Parsing failed"):
        await _run(_UnreachableMinerU(), StubVLLMService())

    [record] = _stage_records(pipeline_log)
    assert record.levelno == logging.ERROR
    assert {key: vars(record)[key] for key in ("stage", "outcome", "error", "cause")} == {
        "stage": "parse",
        "outcome": "error",
        "error": "MinerUError",
        "cause": "ConnectError",
    }
    assert record.exc_info is not None


@pytest.mark.anyio
async def test_every_record_of_a_run_carries_the_checksum(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging("INFO")
    body = json.dumps({"results": {"doc": {"middle_json": json.dumps(STUB_JSON)}}}).encode()
    mineru = MinerUClient("http://mineru:8000", 5.0, 1.0)
    mineru._http = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(200, content=body)),
        base_url="http://mineru:8000",
    )

    await _run(mineru, StubVLLMService())
    await mineru.aclose()
    logging.getLogger("bioparser.tests").warning("after the run")

    *during, after = map(json.loads, capsys.readouterr().err.splitlines())
    assert [record["msg"] for record in during] == [
        "mineru-api request completed",  # from the MinerU client, not the pipeline
        "pipeline stage completed",
        "pipeline stage completed",
        "pipeline stage completed",
    ]
    assert {record["checksum"] for record in during} == {CHECKSUM}
    assert "checksum" not in after


class _CutOffVLLM(StubVLLMService):
    async def generate_json(
        self, prompt: str, *, schema: dict[str, Any], system_prompt: str, max_tokens: int
    ) -> str:
        return '{"observations": [{"quotation": "canary-quote'  # stopped mid-string


class _MangledMinerU(StubMinerUClient):
    async def parse(self, *, filename: str, content: bytes) -> dict[str, Any]:
        middle = copy.deepcopy(STUB_JSON)
        middle["pdf_info"][0]["para_blocks"][0]["lines"][0]["spans"] = ["canary-paper-text"]
        return middle


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mineru", "vllm", "stage"),
    [
        (StubMinerUClient(), _CutOffVLLM(), "extract"),
        (_MangledMinerU(), StubVLLMService(), "map"),
    ],
    ids=["model-output", "paper-text"],
)
async def test_a_failure_traceback_quotes_neither_paper_text_nor_model_output(
    pipeline_log: pytest.LogCaptureFixture, mineru: object, vllm: object, stage: str
) -> None:
    with pytest.raises(PipelineError):
        await _run(mineru, vllm)

    [failure] = [r for r in _stage_records(pipeline_log) if r.levelno == logging.ERROR]
    rendered = JsonFormatter().format(failure)
    assert vars(failure)["stage"] == stage
    assert "ValidationError" in rendered  # the traceback is there ...
    assert "canary" not in rendered  # ... without pydantic quoting its input


def _paper(text: str) -> dict[str, Any]:
    """middle.json with one text block holding [text]."""
    middle = copy.deepcopy(STUB_JSON)
    middle["pdf_info"][0]["para_blocks"][0]["lines"][0]["spans"][0]["content"] = text
    return middle


@pytest.mark.anyio
async def test_a_vllm_error_body_quoting_the_prompt_stays_out_of_the_traceback(
    pipeline_log: pytest.LogCaptureFixture, vllm_transport: Callable[[Handler], None]
) -> None:
    def echoes_the_request(request: httpx2.Request) -> httpx2.Response:
        # A validation error from a FastAPI server (vLLM is one) quotes the request body
        body = request.content.decode()
        return httpx2.Response(400, json={"object": "error", "message": f"invalid: {body}"})

    vllm_transport(echoes_the_request)
    vllm = VLLMService()
    vllm.model = "stub-model"  # skip model discovery
    try:
        with pytest.raises(PipelineError, match="Extraction backend unavailable"):
            await _run(StubMinerUClient(_paper("canary-paper-text: body mass 12.5 g")), vllm)
    finally:
        await vllm.aclose()

    [failure] = [r for r in _stage_records(pipeline_log) if r.levelno == logging.ERROR]
    rendered = JsonFormatter().format(failure)
    assert "BadRequestError" in rendered  # the traceback still names the cause ...
    assert "canary" not in rendered  # ... but not what the server quoted back


@pytest.mark.anyio
async def test_a_schema_failure_names_the_pydantic_error_codes(
    pipeline_log: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(PipelineError, match="did not match the schema"):
        await _run(StubMinerUClient(), _CutOffVLLM())

    [failure] = [r for r in _stage_records(pipeline_log) if r.levelno == logging.ERROR]
    # pydantic's own message is redacted; its error codes (never input) survive in ours
    assert "did not match the extraction schema: json_invalid" in JsonFormatter().format(failure)
