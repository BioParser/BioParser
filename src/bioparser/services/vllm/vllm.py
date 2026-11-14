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
from pydantic import BaseModel

from bioparser.logging_config import stopwatch

from .config import get_settings

LOGGER = logging.getLogger(__name__)


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


class VLLMError(RuntimeError):
    """vLLM was unreachable, timed out, or returned an error status"""


class VLLMTruncatedError(VLLMError):
    """Generation hit max_tokens before the model closed the JSON."""


class VLLMRequestRejectedError(VLLMError):
    """vLLM rejected the request. Retrying the same input will fail again."""


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
        self._max_tokens = settings.vllm_max_tokens
        self._lock = asyncio.Lock()
        self._discovery_timeout = settings.vllm_model_discovery_timeout_seconds

    def _token_ceiling(self, max_tokens: int | None) -> int:
        """The configured ceiling, or a caller's smaller count."""
        if max_tokens is None or max_tokens > self._max_tokens:
            return self._max_tokens
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
                self.model = response.data[0].id
            return self.model

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
            # "length" means max_tokens cut the answer off; generate_model raises on it
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

    async def _chat(
        self,
        prompt: str,
        *,
        system_prompt: str | None,
        temperature: float,
        max_tokens: int | None,
        enable_thinking: bool,
        timeout: float | None,
        response_format: ResponseFormatJSONSchema | None = None,
    ) -> tuple[ChatCompletion, int]:
        """One chat completion. The int is the token ceiling this call sent."""
        token_ceiling = self._token_ceiling(max_tokens)
        request = self.client.chat.completions.create(
            model=await self.get_model(),
            messages=_messages(prompt, system_prompt),
            temperature=temperature,
            max_tokens=token_ceiling,
            extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
            timeout=_request_timeout(timeout),
            response_format=omit if response_format is None else response_format,
        )
        return await self._logged(request), token_ceiling

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
        response, _token_ceiling = await self._chat(
            prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            enable_thinking=enable_thinking,
            timeout=timeout,
        )
        content = response.choices[0].message.content
        if content is None:
            raise RuntimeError("vLLM returned an empty response")
        return content

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
        requested_model = await self.get_model()
        schema: ResponseFormatJSONSchema = {
            "type": "json_schema",
            "json_schema": {
                "name": response_model.__name__,
                "schema": response_model.model_json_schema(),
            },
        }
        try:
            response, token_ceiling = await self._chat(
                prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                enable_thinking=enable_thinking,
                timeout=timeout,
                response_format=schema,
            )
        except NotFoundError as exc:
            async with self._lock:
                if self.model == requested_model:
                    self.model = None
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
                f"generation hit max_tokens={token_ceiling} "
                f"(finish_reason=length); lower Extraction.max_length "
                f"or raise max_tokens within --max-model-len"
            )

        content = choice.message.content
        if content is None:
            raise VLLMError("vLLM returned an empty response")
        return response_model.model_validate_json(content)
