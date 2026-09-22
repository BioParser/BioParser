"""setup_logging(): one JSON handler on the root logger, level from BIOPARSER_LOG_LEVEL.

Also log_context(), error_fields() and redact_url()."""

import contextlib
import importlib
import io
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from bioparser.api import config
from bioparser.api.app import app
from bioparser.logging_config import (
    HANDLER_NAME,
    LogLevel,
    error_fields,
    log_context,
    redact_url,
    setup_logging,
)

# bioparser.api re-exports the FastAPI instance as `app`, which shadows the
# submodule of the same name; import_module returns the module itself.
app_module = importlib.import_module("bioparser.api.app")

LOGGER = logging.getLogger("bioparser.tests")


def _our_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if h.name == HANDLER_NAME]


def test_repeated_setup_keeps_one_handler_and_applies_the_latest_level(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging("INFO")
    setup_logging("DEBUG")

    LOGGER.debug("once")

    assert len(_our_handlers()) == 1
    [line] = capsys.readouterr().err.splitlines()  # a second handler would print it twice
    assert json.loads(line)["msg"] == "once"


@pytest.mark.parametrize(
    ("level", "ours", "third_party"),
    [("DEBUG", logging.DEBUG, logging.WARNING), ("ERROR", logging.ERROR, logging.ERROR)],
)
def test_level_applies_to_bioparser_and_never_lowers_third_party_below_warning(
    level: LogLevel, ours: int, third_party: int
) -> None:
    setup_logging(level)

    assert logging.getLogger("bioparser.services.mineru.client").getEffectiveLevel() == ours
    # httpx2 logs full URLs at INFO, httpcore2 header tuples at DEBUG
    for name in ("httpx2", "httpcore2.http11", "openai"):
        assert logging.getLogger(name).getEffectiveLevel() == third_party


def test_a_record_is_one_line_of_json_with_its_extra_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging("INFO")

    LOGGER.info("parsed %d pages", 12, extra={"status_code": 200, "latency_ms": 12.5})

    [line] = capsys.readouterr().err.splitlines()
    record = json.loads(line)
    ts = record.pop("timestamp")
    assert record == {
        "level": "INFO",
        "logger": "bioparser.tests",
        "msg": "parsed 12 pages",
        "status_code": 200,
        "latency_ms": 12.5,
    }
    assert datetime.fromisoformat(ts).utcoffset() == timedelta(0)


def test_a_line_break_in_a_message_cannot_forge_a_second_entry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging("INFO")
    forged = 'a.pdf\n{"level": "CRITICAL"}\u2028{"level": "CRITICAL"}'

    LOGGER.warning("rejected %s", forged)

    # str.splitlines() also splits on U+2028/U+0085, so this checks those too
    [line] = capsys.readouterr().err.splitlines()
    assert json.loads(line)["msg"] == f"rejected {forged}"


def test_an_exception_is_rendered_inline_with_its_type(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging("INFO")

    try:
        raise ValueError("boom")
    except ValueError:
        LOGGER.exception("stage failed")

    [line] = capsys.readouterr().err.splitlines()
    record = json.loads(line)
    assert record["exc_type"] == "ValueError"
    assert record["exc_info"].startswith("Traceback (most recent call last):")
    assert record["exc_info"].endswith("ValueError: boom")


def test_a_bytes_extra_is_rendered_as_its_size_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging("INFO")

    LOGGER.info("upload received", extra={"content": b"%PDF-1.4 confidential"})

    err = capsys.readouterr().err
    assert json.loads(err)["content"] == "<21 bytes>"
    assert "confidential" not in err


def test_an_extra_cannot_overwrite_a_core_field(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("INFO")

    LOGGER.warning("real", extra={"level": "DEBUG", "logger": "forged"})

    record = json.loads(capsys.readouterr().err)
    assert (record["level"], record["logger"]) == ("WARNING", "bioparser.tests")


def test_uvicorn_records_are_rerouted_through_the_json_handler(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # What uvicorn.config.LOGGING_CONFIG leaves behind: private handlers, propagate=False
    for name in ("uvicorn", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.addHandler(logging.StreamHandler(io.StringIO()))
        uvicorn_logger.propagate = False
        uvicorn_logger.setLevel(logging.INFO)

    setup_logging("WARNING")  # uvicorn keeps its own INFO level
    logging.getLogger("uvicorn.error").info(
        "Uvicorn running", extra={"color_message": "\x1b[1mUvicorn running\x1b[0m"}
    )
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", "/health", "1.1", 200
    )

    running, access = map(json.loads, capsys.readouterr().err.splitlines())
    assert (running["logger"], running["msg"]) == ("uvicorn.error", "Uvicorn running")
    assert "color_message" not in running
    access.pop("timestamp")
    assert access == {
        "level": "INFO",
        "logger": "uvicorn.access",
        "client_addr": "127.0.0.1:5000",
        "method": "GET",
        "path": "/health",
        "http_version": "1.1",
        "status_code": 200,
    }
    assert logging.getLogger("uvicorn").handlers == []
    assert logging.getLogger("uvicorn.access").handlers == []


def test_no_access_log_stays_off() -> None:
    # What uvicorn --no-access-log leaves behind: no handlers, propagate=False
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False

    setup_logging("INFO")

    # uvicorn's own per-connection check for whether to log access lines
    assert access.hasHandlers() is False


def test_an_access_record_of_another_shape_keeps_its_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging("INFO")
    access = logging.getLogger("uvicorn.access")
    access.setLevel(logging.INFO)  # as uvicorn's --log-level sets it

    access.info('%s - "WebSocket %s" %d', "127.0.0.1:5000", "/ws", 101)

    record = json.loads(capsys.readouterr().err)
    assert record["msg"] == '127.0.0.1:5000 - "WebSocket /ws" 101'


def test_log_context_fields_reach_records_from_any_logger(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging("INFO")

    with log_context(checksum="ab12"):
        LOGGER.warning("ours")
        logging.getLogger("httpx2").warning("third party")  # a child of root, not of bioparser
        with log_context(job_id="j1"):
            LOGGER.warning("nested")
        LOGGER.warning("explicit wins", extra={"checksum": "override"})
    LOGGER.warning("after")

    ours, third_party, nested, explicit, after = map(
        json.loads, capsys.readouterr().err.splitlines()
    )
    assert ours["checksum"] == third_party["checksum"] == "ab12"
    assert (nested["checksum"], nested["job_id"]) == ("ab12", "j1")
    assert explicit["checksum"] == "override"
    assert "checksum" not in after


def test_error_fields_name_types_never_messages() -> None:
    try:
        try:
            raise ConnectionError("password=hunter2")
        except ConnectionError as inner:
            raise RuntimeError("password=hunter2") from inner
    except RuntimeError as exc:
        fields = error_fields(exc)

    assert fields == {"error": "RuntimeError", "cause": "ConnectionError"}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://vllm:8000/v1", "http://vllm:8000/v1"),
        ("http://user:hunter2@vllm:8000/v1", "http://***@vllm:8000/v1"),
        ("redis://:hunter2@redis:6379/0", "redis://***@redis:6379/0"),
        ("http://[::1", "***"),  # unparseable: hidden, not echoed
    ],
    ids=["no-userinfo", "user-password", "password-only", "malformed"],
)
def test_redact_url_hides_credentials(url: str, expected: str) -> None:
    assert redact_url(url) == expected


def test_the_handler_follows_sys_stderr_after_it_is_replaced() -> None:
    setup_logging("INFO")  # sys.stderr is pytest's capture stream at this point

    with contextlib.redirect_stderr(io.StringIO()) as replaced:
        LOGGER.warning("after the swap")

    assert json.loads(replaced.getvalue())["msg"] == "after the swap"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, "INFO"), ("debug", "DEBUG"), ("Warning", "WARNING")],
    ids=["unset", "lowercase", "mixed-case"],
)
def test_log_level_comes_from_bioparser_log_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: str | None, expected: str
) -> None:
    monkeypatch.chdir(tmp_path)  # no developer .env: ApiSettings reads ./.env
    if raw is None:
        monkeypatch.delenv("BIOPARSER_LOG_LEVEL", raising=False)
    else:
        monkeypatch.setenv("BIOPARSER_LOG_LEVEL", raw)

    assert config.ApiSettings().log_level == expected


def test_an_unknown_log_level_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIOPARSER_LOG_LEVEL", "verbose")

    with pytest.raises(ValidationError, match="log_level"):
        config.ApiSettings()


class _OfflineVLLM:
    """The lifespan builds a VLLMService and probes its model; skip the network."""

    async def get_model(self) -> str:
        return "stub-model"

    async def aclose(self) -> None:
        return None


def test_lifespan_sets_up_logging_once_across_restarts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "VLLMService", _OfflineVLLM)
    monkeypatch.setattr(
        config,
        "get_api_settings",
        lambda: config.ApiSettings(log_level="DEBUG"),
    )
    # The lifespan overwrites these; registering them lets monkeypatch restore them.
    for name in ("mineru", "vllm"):
        monkeypatch.setattr(app.state, name, None, raising=False)

    for _ in range(2):  # every TestClient context runs the lifespan again
        with TestClient(app):
            pass

    assert len(_our_handlers()) == 1
    assert logging.getLogger("bioparser").level == logging.DEBUG
