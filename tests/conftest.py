import pytest
from bioparser.app import app
from fastapi.testclient import TestClient

from bioparser import jobs


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_job_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "_jobs", {})
