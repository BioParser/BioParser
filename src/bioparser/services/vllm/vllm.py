import asyncio
import logging
from collections.abc import Awaitable

from openai import (
    NOT_GIVEN,
    APIError,
    AsyncOpenAI,
    BadRequestError,
    NotFoundError,
    NotGiven,
    UnprocessableEntityError,
    omit,
)
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from openai.types.shared_params.response_format_json_schema import ResponseFormatJSONSchema
from pydantic import BaseModel, ValidationError

from bioparser.logging_config import stopwatch

from .config import get_settings

LOGGER = logging.getLogger(__name__)


class VLLMError(RuntimeError):
    """vLLM was unreachable, timed out, or returned an error status"""


class VLLMTruncatedError(VLLMError):
    """Generation stopped because it reached max_tokens."""


class VLLMRequestRejectedError(VLLMError):
    """vLLM rejected the request. Retrying the same input will fail again."""


class VLLMSchemaError(VLLMError):
    """The response body did not match the model. The server did accept the request."""


class VLLMService:
    def __init__(self) -> None:
        settings = get_settings()

        self.client = AsyncOpenAI(
            base_url=settings.vllm_base_url,
            api_key=settings.vllm_api_key.get_secret_value(),
            timeout=settings.vllm_request_timeout_seconds,
            max_retries=0,
        )

        self.model: str | None = None
        self._max_model_len: int | None = None
        self._max_tokens = settings.vllm_max_tokens
        self._lock = asyncio.Lock()
        self._discovery_timeout = settings.vllm_model_discovery_timeout_seconds

    def _token_ceiling(self, max_tokens: int | None) -> int:
        """The configured ceiling, or a caller's smaller positive count."""
        if max_tokens is None or max_tokens > self._max_tokens:
            return self._max_tokens
        if max_tokens < 1:
            raise VLLMError("max_tokens must be at least 1")
        return max_tokens

    async def ping(self) -> bool:
        """True when the server answers a models request. An API error means it is down."""
        try:
            await self.client.models.list(timeout=self._discovery_timeout)
        except APIError:
            return False
        return True

    async def get_model(self) -> str:
        """Discover the served model id. The first id the server lists, cached for this process."""
        if self.model is not None:
            return self.model

        async with self._lock:
            if self.model is None:
                try:
                    response = await self.client.models.list(timeout=self._discovery_timeout)
                except APIError as exc:
                    raise VLLMError(f"vLLM unreachable: {type(exc).__name__}") from exc
                if not response.data:
                    raise VLLMError("vLLM exposes no models")
                card = response.data[0]
                self.model = card.id
                self._max_model_len = _reported_max_model_len(card)
            return self.model

    async def get_max_model_len(self) -> int:
        """The served model's context window. vLLM sends this as max_model_len."""
        await self.get_model()
        if self._max_model_len is None:
            raise VLLMError("vLLM did not report max_model_len")
        return self._max_model_len

    def _forget_model(self) -> None:
        """Drop the cached id and context window so the next call discovers both."""
        self.model = None
        self._max_model_len = None

    async def aclose(self) -> None:
        await self.client.close()

    async def _logged(self, request: Awaitable[ChatCompletion]) -> ChatCompletion:
        """Await one chat completion and log one record for it, whatever the outcome.

        Numbers only: latency, finish_reason and token usage. Never the prompt or
        the generated text.
        """
        elapsed_ms = stopwatch()
        try:
            response = await request
        except BaseException as exc:  # observed, never handled: CancelledError included
            LOGGER.warning(
                "vllm request failed",
                extra={"latency_ms": elapsed_ms(), "error": type(exc).__name__},
            )
            raise
        finish_reason = response.choices[0].finish_reason if response.choices else None
        usage = response.usage
        LOGGER.log(
            # "length" means max_tokens cut the answer off; _chat_completion raises on it
            logging.INFO if finish_reason == "stop" else logging.WARNING,
            "vllm completion finished",
            extra={
                "model": response.model,
                "latency_ms": elapsed_ms(),
                "finish_reason": finish_reason,
                "prompt_tokens": usage.prompt_tokens if usage else None,
                "completion_tokens": usage.completion_tokens if usage else None,
            },
        )
        if not response.choices:  # indexing it later would be an IndexError: HTTP 500, not 502
            raise VLLMError("vLLM returned no choices")
        return response

    async def _chat_completion(
        self,
        prompt: str,
        *,
        system_prompt: str | None,
        temperature: float,
        max_tokens: int | None,
        enable_thinking: bool,
        timeout: float | None,
        response_format: ResponseFormatJSONSchema | None = None,
    ) -> str:
        """One chat completion, as text. A 404 forgets the model id that was sent."""
        token_ceiling = self._token_ceiling(max_tokens)
        requested_model = await self.get_model()
        request = self.client.chat.completions.create(
            model=requested_model,
            messages=_messages(prompt, system_prompt),
            temperature=temperature,
            max_tokens=token_ceiling,
            # Qwen thinks unless the call says otherwise, so false is sent too.
            extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
            timeout=_request_timeout(timeout),
            response_format=omit if response_format is None else response_format,
        )
        try:
            response = await self._logged(request)
        except NotFoundError as exc:
            async with self._lock:
                if self.model == requested_model:
                    self._forget_model()
            raise VLLMError(f"vLLM request failed: {type(exc).__name__}") from exc
        except (BadRequestError, UnprocessableEntityError) as exc:
            raise VLLMRequestRejectedError(
                f"vLLM rejected the request: {type(exc).__name__}"
            ) from exc
        except APIError as exc:
            # Connection refused, read timeout, 5xx
            raise VLLMError(f"vLLM request failed: {type(exc).__name__}") from exc

        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise VLLMTruncatedError(
                f"generation hit max_tokens={token_ceiling} (finish_reason=length)."
            )
        content = choice.message.content
        if content is None:
            raise VLLMError("vLLM returned an empty response")
        return content

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        enable_thinking: bool = False,
        timeout: float | None = None,
    ) -> str:
        return await self._chat_completion(
            prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            enable_thinking=enable_thinking,
            timeout=timeout,
        )

    async def generate_model[ModelT: BaseModel](
        self,
        prompt: str,
        response_model: type[ModelT],
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        enable_thinking: bool = False,
        timeout: float | None = None,
    ) -> ModelT:
        """Ask for JSON matching [response_model] and return that model."""
        schema: ResponseFormatJSONSchema = {
            "type": "json_schema",
            "json_schema": {
                "name": response_model.__name__,
                "schema": response_model.model_json_schema(),
            },
        }
        content = await self._chat_completion(
            prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            enable_thinking=enable_thinking,
            timeout=timeout,
            response_format=schema,
        )
        try:
            return response_model.model_validate_json(content)
        except ValidationError as exc:
            codes = sorted({error["type"] for error in exc.errors(include_input=False)})
            raise VLLMSchemaError(f"response did not match the schema: {', '.join(codes)}") from exc


def _reported_max_model_len(card: object) -> int | None:
    """A positive max_model_len from a vLLM model card, or none when it is absent."""
    extra = getattr(card, "model_extra", None)
    if not isinstance(extra, dict):
        return None
    value = extra.get("max_model_len")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _request_timeout(timeout: float | None) -> float | NotGiven:
    """A caller's timeout, or the client's own when the caller does not pass one."""
    if timeout is None:
        return NOT_GIVEN
    return timeout


def _messages(prompt: str, system_prompt: str | None) -> list[ChatCompletionMessageParam]:
    messages: list[ChatCompletionMessageParam] = []
    if system_prompt is not None:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return messages
