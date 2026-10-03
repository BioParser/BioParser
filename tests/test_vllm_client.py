"""VLLMService logging: one record per completion request, numbers only."""

import itertools
import logging
from collections.abc import Callable

import httpx2
import pytest
from stubs import Handler

from bioparser import logging_config
from bioparser.logging_config import JsonFormatter
from bioparser.services.vllm import config as vllm_config
from bioparser.services.vllm import vllm as vllm_module
from bioparser.services.vllm.vllm import VLLMError, VLLMService, VLLMTruncatedError

API_KEY = "sk-canary-api-key-7f3a"
PROMPT = "[b1] canary-prompt-text: adult body mass averaged 12.5 g"
FIELDS = frozenset(
    {"model", "latency_ms", "finish_reason", "prompt_tokens", "completion_tokens", "error"}
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def vllm_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> pytest.LogCaptureFixture:
    """Capture the client's records, with a clock that makes every request take 250 ms."""
    caplog.set_level(logging.INFO, logger=vllm_module.__name__)
    monkeypatch.setattr(logging_config, "perf_counter", itertools.count(100.0, 0.25).__next__)
    return caplog


def _completion(finish_reason: str = "stop") -> dict[str, object]:
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "stub-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": '{"observations": []}'},
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 34, "total_tokens": 154},
    }


@pytest.fixture
def make_service(
    vllm_transport: Callable[[Handler], None],
) -> Callable[[Handler], VLLMService]:
    """A real VLLMService built from Settings, answered by [handler]."""

    def make(handler: Handler) -> VLLMService:
        vllm_transport(handler)
        service = VLLMService()
        service.model = "stub-model"  # skip model discovery
        return service

    return make


async def _generate_json(service: VLLMService) -> str:
    try:
        return await service.generate_json(PROMPT, schema={}, system_prompt="sys", max_tokens=64)
    finally:
        await service.aclose()


def _completions(caplog: pytest.LogCaptureFixture) -> list[tuple[int, dict[str, object]]]:
    """(level, fields) of every record the client logged."""
    return [
        (record.levelno, {k: v for k, v in vars(record).items() if k in FIELDS})
        for record in caplog.records
        if record.name == vllm_module.__name__
    ]


@pytest.mark.anyio
async def test_a_completion_logs_latency_finish_reason_and_usage(
    vllm_log: pytest.LogCaptureFixture, make_service: Callable[[Handler], VLLMService]
) -> None:
    service = make_service(lambda _request: httpx2.Response(200, json=_completion()))

    await _generate_json(service)

    assert _completions(vllm_log) == [
        (
            logging.INFO,
            {
                "model": "stub-model",
                "latency_ms": 250.0,
                "finish_reason": "stop",
                "prompt_tokens": 120,
                "completion_tokens": 34,
            },
        )
    ]


@pytest.mark.anyio
async def test_a_completion_cut_off_by_max_tokens_logs_a_warning(
    vllm_log: pytest.LogCaptureFixture, make_service: Callable[[Handler], VLLMService]
) -> None:
    service = make_service(lambda _request: httpx2.Response(200, json=_completion("length")))

    with pytest.raises(VLLMTruncatedError):
        await _generate_json(service)

    [(level, fields)] = _completions(vllm_log)
    assert (level, fields["finish_reason"]) == (logging.WARNING, "length")


@pytest.mark.anyio
async def test_a_completion_without_choices_is_a_vllm_error(
    vllm_log: pytest.LogCaptureFixture, make_service: Callable[[Handler], VLLMService]
) -> None:
    body = _completion() | {"choices": []}
    service = make_service(lambda _request: httpx2.Response(200, json=body))

    with pytest.raises(VLLMError, match="no choices"):  # the pipeline maps it to 502
        await _generate_json(service)

    [(level, fields)] = _completions(vllm_log)
    assert (level, fields["finish_reason"]) == (logging.WARNING, None)


def _refused(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("connection refused", request=request)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("handler", "error"),
    [
        (
            lambda _request: httpx2.Response(503, json={"error": "overloaded"}),
            "InternalServerError",
        ),
        (_refused, "APIConnectionError"),
    ],
    ids=["503", "unreachable"],
)
async def test_a_failed_request_logs_a_warning_with_the_error_type(
    vllm_log: pytest.LogCaptureFixture,
    make_service: Callable[[Handler], VLLMService],
    handler: Handler,
    error: str,
) -> None:
    service = make_service(handler)

    with pytest.raises(VLLMError):
        await _generate_json(service)

    assert _completions(vllm_log) == [(logging.WARNING, {"latency_ms": 250.0, "error": error})]


@pytest.mark.anyio
@pytest.mark.parametrize("status", [200, 401], ids=["ok", "rejected-key"])
async def test_neither_the_configured_api_key_nor_the_prompt_is_logged(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    vllm_transport: Callable[[Handler], None],
    status: int,
) -> None:
    # Configured the way production is: env -> Settings -> VLLMService -> AsyncOpenAI
    monkeypatch.setenv("BIOPARSER_VLLM_API_KEY", API_KEY)
    caplog.set_level(logging.DEBUG)  # every logger: bioparser, openai and httpx2 included
    sent_headers: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent_headers.append(request.headers["authorization"])
        if request.url.path.endswith("/models"):
            return httpx2.Response(200, json={"object": "list", "data": [{"id": "stub-model"}]})
        body = _completion() if status == 200 else {"error": {"message": "invalid api key"}}
        return httpx2.Response(status, json=body)

    vllm_transport(handler)
    service = VLLMService()  # no preset model: discovery sends the key too
    try:
        await _generate_json(service)
    except VLLMError:
        pass  # the 401 case: only the logs matter here

    # The key really was in play, on discovery and on the completion
    assert sent_headers == [f"Bearer {API_KEY}"] * 2
    rendered = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    assert "vllm completion finished" in rendered or "vllm request failed" in rendered
    assert API_KEY not in rendered
    assert "canary-prompt-text" not in rendered


def test_the_api_key_is_masked_when_settings_are_printed() -> None:
    settings = vllm_config.Settings(vllm_api_key=API_KEY)  # type: ignore[arg-type]

    assert API_KEY not in repr(settings)
    assert API_KEY not in str(settings)
    assert settings.vllm_api_key.get_secret_value() == API_KEY
