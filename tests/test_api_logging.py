"""Logging in the API layer: vLLM model discovery at startup, and pipeline stages."""

import copy
import dataclasses
import importlib
import itertools
import json
import logging
import os
import subprocess
import sys
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from stubs import (
    LOCATABLE_OBSERVATION,
    LOCATABLE_TEXT,
    STUB_JSON,
    Handler,
    StubParser,
    StubVLLMService,
    fake_mineru,
    file_parse_reply,
    stub_parser_artifact,
)

from bioparser import logging_config
from bioparser import pipeline as pipeline_module
from bioparser.api import config
from bioparser.api.app import app
from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME
from bioparser.hashing import sha256_bytes
from bioparser.logging_config import JsonFormatter, setup_logging
from bioparser.parser.errors import ParserProcessError
from bioparser.parser_names import DEFAULT_PARSER_NAME, MINERU_PARSER_NAME
from bioparser.pipeline import PipelineError, PipelineOptions, run_pipeline
from bioparser.services.vllm import config as vllm_config

# bioparser.api re-exports the FastAPI instance as `app`, which shadows the
# submodule of the same name; import_module returns the module itself.
app_module = importlib.import_module("bioparser.api.app")

PDF = b"%PDF-1.4\n%%EOF\n"
OPTIONS = PipelineOptions(
    parser=DEFAULT_PARSER_NAME,
    extractor=DEFAULT_EXTRACTOR_NAME,
    parser_timeout_s=60.0,
    mineru_base_url="http://127.0.0.1:9",
    mineru_tier="basic",
    char_budget=4000,
    max_tokens=256,
)
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


def _app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path | None = None) -> TestClient:
    """TestClient whose lifespan-built clients are dropped again after the test."""
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://localhost:6379/15")
    monkeypatch.setenv("BIOPARSER_DATABASE_URL", "postgresql://bioparser@127.0.0.1:1/bioparser")
    monkeypatch.setenv(
        "BIOPARSER_ARTIFACT_STORAGE_PATH",
        str((tmp_path or Path("/tmp")) / "bioparser-test-artifacts"),
    )
    for name in (
        "vllm",
        "parse_queue",
        "artifact_storage",
        "pipeline_pool",
        "extract_sem",
    ):
        monkeypatch.setattr(app.state, name, None, raising=False)
    api_key = config.get_api_settings().api_key
    headers = {"X-API-Key": api_key.get_secret_value()} if api_key else {}
    return TestClient(app, headers=headers)


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


# What httpx2 raises for a vLLM URL whose password holds an unencoded "/". Kept off
# the raise line: a traceback prints each frame's source line.
_INVALID_PORT = "Invalid port: 'canary-start-of-password'"


def _fails_to_build() -> object:
    raise httpx2.InvalidURL(_INVALID_PORT)


def test_a_failed_startup_is_logged_redacted_and_handed_on_without_its_chain(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(app_module, "VLLMService", _fails_to_build)

    with pytest.raises(app_module.LifespanError) as failure, _app(monkeypatch):
        pass

    [record] = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert (record.getMessage(), vars(record)["phase"], vars(record)["error"]) == (
        "lifespan failed",
        "startup",
        "InvalidURL",
    )
    assert "canary" not in _rendered([record])  # our record: redacted
    # What Starlette formats with traceback.format_exc() for uvicorn to log as text
    handed_on = "".join(traceback.format_exception(failure.value))
    assert "LifespanError: startup failed: InvalidURL" in handed_on
    assert "canary" not in handed_on


def test_uvicorn_logs_no_password_when_the_vllm_url_is_malformed(tmp_path: Path) -> None:
    """The real path: the uvicorn CLI, as in the Dockerfile, with a "/" in the password."""
    env = os.environ | {
        "BIOPARSER_VLLM_BASE_URL": "http://user:canary/rest@127.0.0.1:9/v1",
        "BIOPARSER_REDIS_URL": "redis://localhost:6379/15",
        "BIOPARSER_DATABASE_URL": "postgresql://bioparser@127.0.0.1:1/bioparser",
        "BIOPARSER_ARTIFACT_STORAGE_PATH": str(tmp_path / "artifacts"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "uvicorn", "bioparser.api.app:app", "--port", "0"],
        cwd=tmp_path,  # no developer .env
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode != 0  # startup failed, as it should
    records = [json.loads(line) for line in result.stderr.splitlines()]  # all of it JSON
    assert {"msg": "lifespan failed", "phase": "startup", "error": "InvalidURL"}.items() <= next(
        r for r in records if r["level"] == "CRITICAL"
    ).items()
    assert "canary" not in result.stderr + result.stdout


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


def test_a_malformed_database_url_does_not_echo_its_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "BIOPARSER_DATABASE_URL", "postgresql://bioparser:canary-db-pass@postgres:99999/bioparser"
    )

    with pytest.raises(ValidationError, match="database_url") as failure:
        config.ApiSettings()

    assert "canary-db-pass" not in str(failure.value)


def test_a_malformed_redis_url_does_not_echo_its_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # app.py loads the settings at import, so this error's traceback reaches the
    # container log as plain text, after the JSON record app.py logs about it.
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
    # Threads, not child processes, so the patched OpenAI client below applies to the run
    monkeypatch.setattr(app_module, "_new_pipeline_pool", lambda *_: ThreadPoolExecutor(1))
    monkeypatch.setattr(pipeline_module, "get_parser", lambda *_args, **_kwargs: StubParser())
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
    # discovery at startup; then the run's own discovery and its completion
    assert sent == [f"Bearer {API_KEY}"] * 3
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


def _run(
    monkeypatch: pytest.MonkeyPatch,
    parser: object,
    vllm: object,
    options: PipelineOptions = OPTIONS,
) -> object:
    """run_pipeline() with [parser] as the parser backend (None: the real factory) and [vllm]."""
    if parser is not None:
        monkeypatch.setattr(pipeline_module, "get_parser", lambda *_args, **_kwargs: parser)
    monkeypatch.setattr(pipeline_module, "VLLMService", lambda: vllm)
    return run_pipeline(PDF, options)


def test_each_stage_logs_its_duration_and_results(
    pipeline_log: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(
        monkeypatch,
        StubParser(stub_parser_artifact(LOCATABLE_TEXT)),
        StubVLLMService([LOCATABLE_OBSERVATION]),
    )

    records = _stage_records(pipeline_log)
    assert [(vars(r)["stage"], vars(r)["outcome"], vars(r)["duration_ms"]) for r in records] == [
        ("parse", "ok", 250.0),
        ("extract", "ok", 250.0),
    ]
    assert {r.levelno for r in records} == {logging.INFO}
    parsed, extracted = map(vars, records)
    assert parsed["pages"] == 1
    assert extracted["observations"] == 1


def _unreachable_mineru() -> ParserProcessError:
    error = ParserProcessError("mineru-api unreachable: ConnectError")
    error.__cause__ = httpx2.ConnectError("")
    return error


def test_a_pipeline_failure_names_the_stage_and_the_cause(
    pipeline_log: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(PipelineError, match="Parsing failed"):
        _run(monkeypatch, StubParser(error=_unreachable_mineru()), StubVLLMService())

    [record] = _stage_records(pipeline_log)
    assert record.levelno == logging.ERROR
    assert {key: vars(record)[key] for key in ("stage", "outcome", "error", "cause")} == {
        "stage": "parse",
        "outcome": "error",
        "error": "ParserProcessError",
        "cause": "ConnectError",
    }
    assert record.exc_info is not None


def test_every_record_of_a_run_carries_the_checksum(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_logging("INFO")
    # The real MinerU parser, so a record from another logger is in the run too
    with fake_mineru(file_parse_reply()) as (url, _seen):
        options = dataclasses.replace(OPTIONS, parser=MINERU_PARSER_NAME, mineru_base_url=url)
        _run(monkeypatch, None, StubVLLMService(), options)
    logging.getLogger("bioparser.tests").warning("after the run")

    *during, after = map(json.loads, capsys.readouterr().err.splitlines())
    assert [record["msg"] for record in during] == [
        "mineru-api request completed",  # from the MinerU client, not the pipeline
        "pipeline stage completed",
        "pipeline stage completed",
    ]
    assert {record["checksum"] for record in during} == {sha256_bytes(PDF)}
    assert "checksum" not in after


class _CutOffVLLM(StubVLLMService):
    async def generate_json(
        self, prompt: str, *, schema: dict[str, Any], system_prompt: str, max_tokens: int
    ) -> str:
        return '{"observations": [{"quotation": "canary-quote'  # stopped mid-string


def test_a_failure_traceback_quotes_no_model_output(
    pipeline_log: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(PipelineError):
        _run(monkeypatch, StubParser(), _CutOffVLLM())

    [failure] = [r for r in _stage_records(pipeline_log) if r.levelno == logging.ERROR]
    rendered = JsonFormatter().format(failure)
    assert vars(failure)["stage"] == "extract"
    assert "ValidationError" in rendered  # the traceback is there ...
    assert "canary" not in rendered  # ... without pydantic quoting its input


def test_a_failure_traceback_quotes_no_paper_text(
    pipeline_log: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    middle = copy.deepcopy(STUB_JSON)
    middle["pages"][0]["blocks"][0]["content"] = ["canary-paper-text"]
    with fake_mineru(file_parse_reply(middle)) as (url, _seen):
        options = dataclasses.replace(OPTIONS, parser=MINERU_PARSER_NAME, mineru_base_url=url)
        with pytest.raises(PipelineError):
            _run(monkeypatch, None, StubVLLMService(), options)

    [failure] = [r for r in _stage_records(pipeline_log) if r.levelno == logging.ERROR]
    rendered = JsonFormatter().format(failure)
    assert vars(failure)["stage"] == "parse"
    assert "ValidationError" in rendered  # the traceback is there ...
    assert "canary" not in rendered  # ... without pydantic quoting its input


def test_a_vllm_error_body_quoting_the_prompt_stays_out_of_the_traceback(
    pipeline_log: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    vllm_transport: Callable[[Handler], None],
) -> None:
    def echoes_the_request(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/models"):
            return httpx2.Response(200, json=MODELS)
        # A validation error from a FastAPI server (vLLM is one) quotes the request body
        body = request.content.decode()
        return httpx2.Response(400, json={"object": "error", "message": f"invalid: {body}"})

    vllm_transport(echoes_the_request)
    monkeypatch.setattr(
        pipeline_module,
        "get_parser",
        lambda *_args, **_kwargs: StubParser(
            stub_parser_artifact("canary-paper-text: body mass 12.5 g")
        ),
    )
    with pytest.raises(PipelineError, match="Extraction request rejected"):
        run_pipeline(PDF, OPTIONS)  # the real VLLMService, on the patched transport

    [failure] = [r for r in _stage_records(pipeline_log) if r.levelno == logging.ERROR]
    rendered = JsonFormatter().format(failure)
    assert "BadRequestError" in rendered  # the traceback still names the cause ...
    assert "canary" not in rendered  # ... but not what the server quoted back


def test_a_schema_failure_names_the_pydantic_error_codes(
    pipeline_log: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(PipelineError, match="did not match the schema"):
        _run(monkeypatch, StubParser(), _CutOffVLLM())

    [failure] = [r for r in _stage_records(pipeline_log) if r.levelno == logging.ERROR]
    # pydantic's own message is redacted; its error codes (never input) survive in ours
    assert "did not match the extraction schema: json_invalid" in JsonFormatter().format(failure)
