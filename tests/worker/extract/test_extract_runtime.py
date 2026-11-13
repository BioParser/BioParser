import json
import runpy
from pathlib import Path
from threading import Event
from urllib.request import urlopen

import pytest

from bioparser.worker.extract import runtime


def test_python_dash_m_runs_the_worker_main(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(runtime, "main", lambda: called.append(True))

    runpy.run_module("bioparser.worker.extract", run_name="__main__")

    assert called == [True]


class _Queue:
    def consume(self, handler: object, *, stop: Event) -> None:
        with urlopen("http://127.0.0.1:8081/health", timeout=3) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "ok"}


class _Handler:
    def close(self) -> None:
        pass


def test_main_serves_health_while_the_queue_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BIOPARSER_DATABASE_URL", "postgresql://localhost/bioparser")
    monkeypatch.setenv("BIOPARSER_ARTIFACT_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("BIOPARSER_EXTRACT_QUEUE_NAME", "extract")
    monkeypatch.setattr(runtime, "build_worker", lambda settings: (_Queue(), _Handler()))
    monkeypatch.setattr(runtime, "install_shutdown", lambda stop: None)

    runtime.main()
