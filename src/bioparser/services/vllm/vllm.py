import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

from openai import APIError, AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from bioparser.logging_config import stopwatch

from .config import get_settings

logger = logging.getLogger(__name__)


class VLLMError(RuntimeError):
    """vLLM was unreachable, timed out, or returned an error status"""


class VLLMTruncatedError(VLLMError):
    """Generation hit max_tokens before the model closed the JSON."""


class VLLMService:
    def __init__(self) -> None:
        settings = get_settings()

        self.client = AsyncOpenAI(
            base_url=settings.vllm_base_url,
            api_key=settings.vllm_api_key.get_secret_value(),
            timeout=settings.request_timeout_seconds,
            max_retries=0,
        )

        self.model: str | None = None
        self._lock = asyncio.Lock()
        self._discovery_timeout = settings.model_discovery_timeout_seconds

    async def get_model(self) -> str:
        """Discover the served model id - used once per process"""
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
            logger.warning(
                "vllm request failed",
                extra={"latency_ms": elapsed_ms(), "error": type(exc).__name__},
            )
            raise
        finish_reason = response.choices[0].finish_reason if response.choices else None
        usage = response.usage
        logger.log(
            # "length" means max_tokens cut the answer off; generate_json raises on it
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

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        max_tokens: int,
    ) -> str:

        messages: list[ChatCompletionMessageParam] = []

        if system_prompt is not None:
            messages.append(
                {
                    "role": "system",
                    "content": system_prompt,
                }
            )

        messages.append(
            {
                "role": "user",
                "content": prompt,
            }
        )

        response = await self._logged(
            self.client.chat.completions.create(
                model=await self.get_model(),
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        )

        content = response.choices[0].message.content

        if content is None:
            raise RuntimeError("vLLM returned an empty response")

        return content

    async def generate_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        system_prompt: str,
        max_tokens: int,
    ) -> str:
        try:
            response = await self._logged(
                self.client.chat.completions.create(
                    model=await self.get_model(),
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,
                    max_tokens=max_tokens,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {"name": "extraction", "schema": schema},
                    },
                    # Qwen3 emits <think> blocks; disable it
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
            )
        except APIError as exc:
            # Connection refused, read timeout, 5xx
            raise VLLMError(f"vLLM request failed: {type(exc).__name__}") from exc

        choice = response.choices[0]

        if choice.finish_reason == "length":
            raise VLLMTruncatedError(
                f"generation hit max_tokens={max_tokens} "
                f"(finish_reason=length); lower Extraction.max_length "
                f"or raise max_tokens within --max-model-len"
            )

        content = choice.message.content
        if content is None:
            raise VLLMError("vLLM returned an empty response")
        return content
