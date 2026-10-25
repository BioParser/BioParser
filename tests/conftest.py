import asyncio
import logging
from collections.abc import Callable, Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from stubs import (
    Handler,
    InlinePipeline,
    StubArtifactStorage,
    StubJobStateStore,
    StubParseQueue,
    StubParser,
)

from bioparser import pipeline as pipeline_module
from bioparser.api import config
from bioparser.api.app import app
from bioparser.logging_config import HANDLER_NAME
from bioparser.services.vllm import config as vllm_config
from bioparser.services.vllm import vllm as vllm_module

TEST_API_KEY = "test-api-key-at-least-32-characters-long"


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
    return TestClient(app, headers={"X-API-Key": TEST_API_KEY})


@pytest.fixture
def inline_pipeline(monkeypatch: pytest.MonkeyPatch) -> Iterator[InlinePipeline]:
    """Stub parser and vLLM for the pipeline. /extract runs it in threads of this process,
    not in child processes, so the stubs apply."""
    # TestClient(app) without `with` runs no lifespan, so the test sets the pool.
    inline = InlinePipeline()

    def get_parser(name: str, **kwargs: Any) -> StubParser:
        inline.parser_requests.append((name, kwargs))
        return inline.parser

    monkeypatch.setattr(pipeline_module, "get_parser", get_parser)
    monkeypatch.setattr(pipeline_module, "VLLMService", lambda: inline.vllm)
    pool = ThreadPoolExecutor(max_workers=4)
    monkeypatch.setattr(app.state, "pipeline_pool", pool, raising=False)
    yield inline
    pool.shutdown(wait=True)


@pytest.fixture(autouse=True)
def default_test_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a default API key in the test suite so general endpoint
    tests authenticate successfully. Specific auth tests can override this."""
    monkeypatch.setenv("BIOPARSER_API_KEY", TEST_API_KEY)


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
