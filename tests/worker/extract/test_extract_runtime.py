import runpy

import pytest

from bioparser.worker.extract import runtime


def test_python_dash_m_runs_the_worker_main(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(runtime, "main", lambda: called.append(True))

    runpy.run_module("bioparser.worker.extract", run_name="__main__")

    assert called == [True]
