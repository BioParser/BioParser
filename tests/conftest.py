import asyncio

import pytest
from fastapi.testclient import TestClient
from stubs import StubMinerUClient, StubVLLMService

from bioparser.api import config, jobs
from bioparser.api.app import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def stub_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[StubMinerUClient, StubVLLMService]:
    """Stub network hops for /extract"""
    # NOTE:  Here TestClient(app) is not used as a context manager, so lifespan doesn't
    # run, and app.state.mineru / app.state.vllm are unset

    mineru = StubMinerUClient()
    vllm = StubVLLMService()
    monkeypatch.setattr(app.state, "mineru", mineru, raising=False)
    monkeypatch.setattr(app.state, "vllm", vllm, raising=False)
    return mineru, vllm


@pytest.fixture(autouse=True)
def isolated_job_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "_jobs", {})


@pytest.fixture(autouse=True)
def extract_semaphore(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = config.get_api_settings()
    monkeypatch.setattr(
        app.state,
        "extract_sem",
        asyncio.Semaphore(settings.extract_concurrency),
        raising=False,
    )
