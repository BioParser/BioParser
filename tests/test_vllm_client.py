"""VLLMService logging: one record per completion request, numbers only."""

import itertools
import json
import logging
from collections.abc import Callable

import httpx2
import pytest
from pydantic import BaseModel
from stubs import Handler

from bioparser import logging_config
from bioparser.logging_config import JsonFormatter
from bioparser.services.vllm import config as vllm_config
from bioparser.services.vllm import vllm as vllm_module
from bioparser.services.vllm.vllm import (
    VLLMError,
    VLLMRequestRejectedError,
    VLLMSchemaError,
    VLLMService,
    VLLMTruncatedError,
)

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


def _completion(
    finish_reason: str = "stop", content: str = '{"observations": []}'
) -> dict[str, object]:
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "stub-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
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


class _Reply(BaseModel):
    observations: list[object] = []


async def _generate_model(service: VLLMService) -> _Reply:
    try:
        return await service.generate_model(PROMPT, _Reply, system_prompt="sys", max_tokens=64)
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

    await _generate_model(service)

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
        await _generate_model(service)

    [(level, fields)] = _completions(vllm_log)
    assert (level, fields["finish_reason"]) == (logging.WARNING, "length")


@pytest.mark.anyio
async def test_a_body_that_does_not_match_the_model_is_a_schema_error(
    make_service: Callable[[Handler], VLLMService],
) -> None:
    service = make_service(
        lambda _request: httpx2.Response(200, json=_completion(content="canary-not-json"))
    )

    with pytest.raises(VLLMSchemaError, match="json_invalid") as raised:
        await _generate_model(service)

    assert "canary-not-json" not in str(raised.value)


@pytest.mark.anyio
async def test_a_completion_without_choices_is_a_vllm_error(
    vllm_log: pytest.LogCaptureFixture, make_service: Callable[[Handler], VLLMService]
) -> None:
    body = _completion() | {"choices": []}
    service = make_service(lambda _request: httpx2.Response(200, json=body))

    with pytest.raises(VLLMError, match="no choices"):  # the pipeline maps it to 502
        await _generate_model(service)

    [(level, fields)] = _completions(vllm_log)
    assert (level, fields["finish_reason"]) == (logging.WARNING, None)


@pytest.mark.anyio
@pytest.mark.parametrize("status", [400, 422])
async def test_a_rejected_request_is_not_an_outage(
    vllm_log: pytest.LogCaptureFixture,
    make_service: Callable[[Handler], VLLMService],
    status: int,
) -> None:
    service = make_service(lambda _request: httpx2.Response(status, json={"error": "rejected"}))

    with pytest.raises(VLLMRequestRejectedError):
        await _generate_model(service)

    [(level, fields)] = _completions(vllm_log)
    assert level == logging.WARNING
    assert fields["error"] in {"BadRequestError", "UnprocessableEntityError"}
    assert fields["latency_ms"] == 250.0


def _models(data: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {"object": "list", "data": [{"id": "stub-model"}] if data is None else data}


@pytest.mark.anyio
async def test_ping_is_true_when_the_server_lists_models(
    make_service: Callable[[Handler], VLLMService],
) -> None:
    service = make_service(lambda _request: httpx2.Response(200, json=_models()))

    assert await service.ping() is True
    await service.aclose()


@pytest.mark.anyio
async def test_ping_is_false_when_the_server_is_down(
    make_service: Callable[[Handler], VLLMService],
) -> None:
    service = make_service(lambda _request: httpx2.Response(503, json={"error": "down"}))

    assert await service.ping() is False
    await service.aclose()


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
        await _generate_model(service)

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
        await _generate_model(service)
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


async def _ask(service: VLLMService, method: str) -> str | _Reply:
    if method == "generate":
        return await service.generate(PROMPT, max_tokens=64)
    return await service.generate_model(PROMPT, _Reply, system_prompt="sys", max_tokens=64)


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["generate", "generate_model"])
async def test_a_404_clears_the_cached_model_and_rediscovers_it(
    vllm_transport: Callable[[Handler], None],
    method: str,
) -> None:
    model_discoveries = 0
    requested_models: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal model_discoveries

        if request.url.path.endswith("/models"):
            model_discoveries += 1
            model = "old-model" if model_discoveries == 1 else "new-model"
            return httpx2.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"id": model}],
                },
            )

        body = json.loads(request.content)
        requested_models.append(body["model"])

        if body["model"] == "old-model":
            return httpx2.Response(
                404,
                json={
                    "error": {
                        "message": "model not found",
                        "type": "invalid_request_error",
                        "code": "model_not_found",
                    }
                },
            )

        return httpx2.Response(
            200,
            json=_completion() | {"model": "new-model"},
        )

    vllm_transport(handler)
    service = VLLMService()

    try:
        with pytest.raises(VLLMError):
            await _ask(service, method)

        assert service.model is None

        result = await _ask(service, method)
    finally:
        await service.aclose()

    assert result == ('{"observations": []}' if method == "generate" else _Reply())
    assert model_discoveries == 2
    assert requested_models == ["old-model", "new-model"]


def test_the_client_has_no_concurrency_cap() -> None:
    assert "max_concurrency" not in vllm_config.Settings.model_fields
    assert "vllm_model" not in vllm_config.Settings.model_fields
    assert "vllm_enable_thinking" not in vllm_config.Settings.model_fields


def _chat(
    bodies: list[dict[str, object]],
    *,
    status: int = 200,
    model: str = "stub-model",
) -> Handler:
    """Record each chat body. Discovery answers with [model]."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/models"):
            return httpx2.Response(200, json={"object": "list", "data": [{"id": model}]})
        body = json.loads(request.content)
        bodies.append(body)
        if status != 200:
            return httpx2.Response(
                status,
                json={
                    "error": {
                        "message": "model not found",
                        "type": "invalid_request_error",
                        "code": "model_not_found",
                    }
                },
            )
        return httpx2.Response(200, json=_completion() | {"model": model})

    return handler


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", [False, True], ids=["off", "on"])
async def test_a_call_sends_its_own_thinking_flag(
    monkeypatch: pytest.MonkeyPatch,
    vllm_transport: Callable[[Handler], None],
    enabled: bool,
) -> None:
    monkeypatch.setenv("BIOPARSER_VLLM_MAX_TOKENS", "1024")
    bodies: list[dict[str, object]] = []
    vllm_transport(_chat(bodies))
    service = VLLMService()
    try:
        await service.generate_model(
            PROMPT, _Reply, system_prompt="sys", max_tokens=64, enable_thinking=enabled
        )
        await service.generate("hello", max_tokens=64, enable_thinking=enabled)
    finally:
        await service.aclose()

    assert bodies[0]["chat_template_kwargs"] == {"enable_thinking": enabled}
    assert bodies[1]["chat_template_kwargs"] == {"enable_thinking": enabled}


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["generate", "generate_model"])
async def test_an_omitted_token_ceiling_uses_the_client_setting(
    monkeypatch: pytest.MonkeyPatch,
    vllm_transport: Callable[[Handler], None],
    method: str,
) -> None:
    monkeypatch.setenv("BIOPARSER_VLLM_MAX_TOKENS", "256")
    bodies: list[dict[str, object]] = []
    vllm_transport(_chat(bodies))
    service = VLLMService()
    try:
        if method == "generate":
            await service.generate("hello")
        else:
            await service.generate_model(PROMPT, _Reply, system_prompt="sys")
    finally:
        await service.aclose()

    assert bodies[0]["max_tokens"] == 256


@pytest.mark.anyio
async def test_a_caller_may_ask_for_fewer_tokens_than_the_ceiling(
    monkeypatch: pytest.MonkeyPatch,
    vllm_transport: Callable[[Handler], None],
) -> None:
    monkeypatch.setenv("BIOPARSER_VLLM_MAX_TOKENS", "256")
    bodies: list[dict[str, object]] = []
    vllm_transport(_chat(bodies))
    service = VLLMService()
    try:
        await service.generate("hello", max_tokens=64)
        await service.generate_model(PROMPT, _Reply, system_prompt="sys", max_tokens=32)
        await service.generate("hello", max_tokens=999)
    finally:
        await service.aclose()

    assert [body["max_tokens"] for body in bodies] == [64, 32, 256]


@pytest.mark.anyio
@pytest.mark.parametrize("max_tokens", [0, -1])
async def test_a_non_positive_token_count_is_rejected(
    make_service: Callable[[Handler], VLLMService],
    max_tokens: int,
) -> None:
    service = make_service(lambda _request: httpx2.Response(500, json={"error": "unused"}))
    try:
        with pytest.raises(VLLMError, match="at least 1"):
            await service.generate("hello", max_tokens=max_tokens)
    finally:
        await service.aclose()
