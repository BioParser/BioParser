import json
import logging
import runpy
import signal
from pathlib import Path
from threading import Event

import pytest

from bioparser.worker.parse import runtime
from bioparser.worker.parse.runtime import install_shutdown


@pytest.fixture
def restore_signals() -> object:
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_signal_sets_stop_event(restore_signals: None, sig: signal.Signals) -> None:
    stop = Event()
    install_shutdown(stop)
    assert not stop.is_set()

    signal.raise_signal(sig)

    assert stop.is_set()


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
    monkeypatch.setenv("BIOPARSER_ARTIFACT_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("BIOPARSER_PARSE_QUEUE_NAME", "parse")
    monkeypatch.delenv("BIOPARSER_LOG_LEVEL", raising=False)
    monkeypatch.setattr(runtime, "build_worker", lambda settings: (_LoggingQueue(), _Handler()))
    monkeypatch.setattr(runtime, "install_shutdown", lambda stop: None)
    monkeypatch.setattr(runtime, "require_mineru_cli", lambda: None)

    runtime.main()

    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert [(r["logger"], r["level"], r["msg"]) for r in records] == [
        ("bioparser.tests", "INFO", "bioparser info"),
        ("dramatiq.worker", "CRITICAL", "dramatiq critical"),
    ]
    assert logging.getLogger("dramatiq").getEffectiveLevel() == logging.WARNING


def _worker_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BIOPARSER_ARTIFACT_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("BIOPARSER_PARSE_QUEUE_NAME", "parse")


def test_main_refuses_to_start_without_the_mineru_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _worker_env(tmp_path, monkeypatch)

    def _missing() -> None:
        raise RuntimeError("missing")

    def _must_not_start(settings: object) -> tuple[object, object]:
        raise AssertionError("started")

    monkeypatch.setattr(runtime, "require_mineru_cli", _missing)
    monkeypatch.setattr(runtime, "build_worker", _must_not_start)

    with pytest.raises(RuntimeError, match="missing"):
        runtime.main()


def test_python_dash_m_runs_the_worker_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """The container command is `python -m bioparser.worker.parse`."""
    called: list[bool] = []
    monkeypatch.setattr(runtime, "main", lambda: called.append(True))

    runpy.run_module("bioparser.worker.parse", run_name="__main__")

    assert called == [True]
