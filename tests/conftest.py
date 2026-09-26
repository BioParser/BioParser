import asyncio
import logging
from collections.abc import Callable, Generator, Iterator
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from stubs import Handler, StubMinerUClient, StubVLLMService

from bioparser.api import config, jobs
from bioparser.api.app import app
from bioparser.logging_config import HANDLER_NAME
from bioparser.services.vllm import config as vllm_config
from bioparser.services.vllm import vllm as vllm_module


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
def reset_vllm_settings_cache() -> Generator[None]:
    """vLLM Settings are cached per process: a test that sets BIOPARSER_VLLM_* must
    not hand its values (e.g. a canary API key) to the tests after it."""
    if hasattr(vllm_config.get_settings, "cache_clear"):
        vllm_config.get_settings.cache_clear()
    yield
    if hasattr(vllm_config.get_settings, "cache_clear"):
        vllm_config.get_settings.cache_clear()


@pytest.fixture
def vllm_transport(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], None]:
    """Answer every VLLMService built afterwards with [handler] instead of the network.

    Only the transport is swapped. Base URL, API key and timeouts still come from
    the configured Settings, so a test sees what production would send and log.
    """

    def install(handler: Handler) -> None:
        def client(**kwargs: Any) -> AsyncOpenAI:
            transport = httpx2.MockTransport(handler)
            return AsyncOpenAI(**kwargs, http_client=httpx2.AsyncClient(transport=transport))

        monkeypatch.setattr(vllm_module, "AsyncOpenAI", client)

    return install


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


TOUCHED_LOGGERS = ("bioparser", "uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """setup_logging (run by every lifespan) changes process-global state; undo it.

    Root handlers are not restored wholesale: pytest swaps its own capture
    handlers on the root logger between test phases, so only ours is removed.
    """
    root = logging.getLogger()
    root_level = root.level
    saved = [
        (logger, logger.level, logger.handlers[:], logger.propagate)
        for logger in map(logging.getLogger, TOUCHED_LOGGERS)
    ]
    yield
    for handler in [h for h in root.handlers if h.name == HANDLER_NAME]:
        root.removeHandler(handler)
    root.setLevel(root_level)
    for logger, level, handlers, propagate in saved:
        logger.setLevel(level)
        logger.handlers[:] = handlers
        logger.propagate = propagate
