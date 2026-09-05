import pytest
from fastapi.testclient import TestClient

from bioparser import jobs
from bioparser.app import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_job_store(monkeypatch):
    monkeypatch.setattr(jobs, "_jobs", {})
