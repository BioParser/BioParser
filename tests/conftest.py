import pytest
from fastapi.testclient import TestClient

from bioparser.api import jobs
from bioparser.api.app import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_job_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "_jobs", {})
