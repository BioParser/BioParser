"""VLLMService logging: one record per completion request, numbers only."""

import itertools
import logging

import httpx2
import pytest
from openai import AsyncOpenAI

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
    monkeypatch.setattr(vllm_module, "perf_counter", itertools.count(100.0, 0.25).__next__)
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


def _service(handler: object) -> VLLMService:
    service = VLLMService()
    service.client = AsyncOpenAI(
        base_url="http://vllm:8000/v1",
        api_key=API_KEY,
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),  # type: ignore[arg-type]
    )
    service.model = "stub-model"  # skip model discovery
    return service


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
    vllm_log: pytest.LogCaptureFixture,
) -> None:
    service = _service(lambda _request: httpx2.Response(200, json=_completion()))

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
    vllm_log: pytest.LogCaptureFixture,
) -> None:
    service = _service(lambda _request: httpx2.Response(200, json=_completion("length")))

    with pytest.raises(VLLMTruncatedError):
        await _generate_json(service)

    [(level, fields)] = _completions(vllm_log)
    assert (level, fields["finish_reason"]) == (logging.WARNING, "length")


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
    vllm_log: pytest.LogCaptureFixture, handler: object, error: str
) -> None:
    service = _service(handler)

    with pytest.raises(VLLMError):
        await _generate_json(service)

    assert _completions(vllm_log) == [(logging.WARNING, {"latency_ms": 250.0, "error": error})]


@pytest.mark.anyio
@pytest.mark.parametrize("status", [200, 401], ids=["ok", "rejected-key"])
async def test_neither_the_api_key_nor_the_prompt_is_logged(
    caplog: pytest.LogCaptureFixture, status: int
) -> None:
    caplog.set_level(logging.DEBUG)  # every logger, openai/httpx2/httpcore2 included
    sent_headers: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent_headers.append(request.headers["authorization"])
        body = _completion() if status == 200 else {"error": {"message": "invalid api key"}}
        return httpx2.Response(status, json=body)

    try:
        await _generate_json(_service(handler))
    except VLLMError:
        pass  # the 401 case: only the logs matter here

    assert sent_headers == [f"Bearer {API_KEY}"]  # the key really was in play
    rendered = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    assert "vllm completion finished" in rendered or "vllm request failed" in rendered
    assert API_KEY not in rendered
    assert "canary-prompt-text" not in rendered


def test_the_api_key_is_masked_when_settings_are_printed() -> None:
    settings = vllm_config.Settings(vllm_api_key=API_KEY)  # type: ignore[arg-type]

    assert API_KEY not in repr(settings)
    assert API_KEY not in str(settings)
    assert settings.vllm_api_key.get_secret_value() == API_KEY
