import asyncio
from collections.abc import Generator

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
def reset_api_settings_cache() -> Generator[None]:
    if hasattr(config.get_api_settings, "cache_clear"):
        config.get_api_settings.cache_clear()
    yield
    if hasattr(config.get_api_settings, "cache_clear"):
        config.get_api_settings.cache_clear()


@pytest.fixture(autouse=True)
def isolated_job_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "_jobs", {})


@pytest.fixture
def extract_semaphore(
    reset_api_settings_cache: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = config.get_api_settings()
    monkeypatch.setattr(
        app.state,
        "extract_sem",
        asyncio.Semaphore(settings.extract_concurrency),
        raising=False,
    )
