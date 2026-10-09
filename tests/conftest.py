import asyncio
import logging
from collections.abc import Callable, Generator, Iterator
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from stubs import (
    Handler,
    StubArtifactStorage,
    StubJobStateStore,
    StubMinerUClient,
    StubParseQueue,
    StubVLLMService,
)

from bioparser.api import config
from bioparser.api.app import app
from bioparser.logging_config import HANDLER_NAME
from bioparser.services.vllm import config as vllm_config
from bioparser.services.vllm import vllm as vllm_module


@pytest.fixture
def stub_api_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]]:
    events: list[str] = []
    artifact_storage = StubArtifactStorage(events)
    job_store = StubJobStateStore(events)
    parse_queue = StubParseQueue(events)

    monkeypatch.setattr(app.state, "artifact_storage", artifact_storage, raising=False)
    monkeypatch.setattr(app.state, "job_store", job_store, raising=False)
    monkeypatch.setattr(app.state, "parse_queue", parse_queue, raising=False)
    return artifact_storage, job_store, parse_queue, events


@pytest.fixture
def client(
    stub_api_adapters: tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]],
) -> TestClient:
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
def reset_vllm_settings_cache() -> Generator[None]:
    if hasattr(vllm_config.get_settings, "cache_clear"):
        vllm_config.get_settings.cache_clear()
    yield
    if hasattr(vllm_config.get_settings, "cache_clear"):
        vllm_config.get_settings.cache_clear()


@pytest.fixture
def vllm_transport(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], None]:
    def install(handler: Handler) -> None:
        def client(**kwargs: Any) -> AsyncOpenAI:
            return AsyncOpenAI(
                **kwargs,
                http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
            )

        monkeypatch.setattr(vllm_module, "AsyncOpenAI", client)

    return install


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


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    root_level = root.level
    touched = ("bioparser", "uvicorn", "uvicorn.error", "uvicorn.access")
    saved = [
        (logger, logger.level, logger.handlers[:], logger.propagate)
        for logger in map(logging.getLogger, touched)
    ]
    yield
    for handler in [handler for handler in root.handlers if handler.name == HANDLER_NAME]:
        root.removeHandler(handler)
    root.setLevel(root_level)
    for logger, level, handlers, propagate in saved:
        logger.setLevel(level)
        logger.handlers[:] = handlers
        logger.propagate = propagate
