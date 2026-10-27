import json
import logging
import runpy
from pathlib import Path
from threading import Event

import pytest
from stubs import fake_mineru, file_parse_reply

from bioparser.worker.parse import runtime
from bioparser.worker.parse.config import WorkerSettings
from bioparser.worker.parse.runtime import build_worker


class _LoggingQueue:
    """Stands in for the Redis queue: logs instead of consuming."""

    def consume(self, handler: object, *, stop: Event) -> None:
        logging.getLogger("bioparser.tests").info("bioparser info")
        logging.getLogger("dramatiq.worker").critical("dramatiq critical")
        logging.getLogger("dramatiq.worker").info("dramatiq info")


class _Handler:
    def close(self) -> None:
        pass


def test_main_logs_json_to_stderr_with_dramatiq_at_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)  # no developer .env
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BIOPARSER_DATABASE_URL", "postgresql://localhost/bioparser")
    monkeypatch.setenv("BIOPARSER_ARTIFACT_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("BIOPARSER_PARSE_QUEUE_NAME", "parse")
    monkeypatch.setenv("BIOPARSER_EXTRACT_QUEUE_NAME", "extract")
    monkeypatch.delenv("BIOPARSER_LOG_LEVEL", raising=False)
    monkeypatch.setattr(runtime, "build_worker", lambda settings: (_LoggingQueue(), _Handler()))
    monkeypatch.setattr(runtime, "install_shutdown", lambda stop: None)

    runtime.main()

    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert [(r["logger"], r["level"], r["msg"]) for r in records] == [
        ("bioparser.tests", "INFO", "bioparser info"),
        ("dramatiq.worker", "CRITICAL", "dramatiq critical"),
    ]
    assert logging.getLogger("dramatiq").getEffectiveLevel() == logging.WARNING


def test_python_dash_m_runs_the_worker_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """The container command is `python -m bioparser.worker.parse`."""
    called: list[bool] = []
    monkeypatch.setattr(runtime, "main", lambda: called.append(True))

    runpy.run_module("bioparser.worker.parse", run_name="__main__")

    assert called == [True]


def test_one_tier_setting_names_the_request_and_the_result_path(tmp_path: Path) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    with fake_mineru(file_parse_reply()) as (url, seen):
        settings = WorkerSettings(
            redis_url="redis://127.0.0.1:1/0",  # type: ignore[arg-type]  # never reached
            database_url="postgresql://localhost/bioparser",  # type: ignore[arg-type]
            artifact_storage_path=tmp_path,
            parse_queue_name="parse",
            extract_queue_name="extract",
            mineru_base_url=url,  # type: ignore[arg-type]
            mineru_tier="standard",
        )
        _queue, handler = build_worker(settings)
        artifact = handler._parser_factory("mineru").parse(pdf)
        handler.close()

    assert "tier=standard&" in seen[0]
    assert artifact.parser.configuration["tier"] == handler._mineru_tier == "standard"
