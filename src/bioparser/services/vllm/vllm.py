import asyncio
from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from .config import get_settings


class VLLMTruncatedError(RuntimeError):
    """Generation hit max_tokens before the model closed the JSON."""


# TODO: expand and add logger
class VLLMService:
    def __init__(self) -> None:
        settings = get_settings()

        self.client = AsyncOpenAI(
            base_url=settings.vllm_base_url,
            api_key=settings.vllm_api_key,
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
                response = await self.client.models.list(timeout=self._discovery_timeout)
                if not response.data:
                    raise RuntimeError("vLLM exposes no models")
                self.model = response.data[0].id
            return self.model

    async def aclose(self) -> None:
        await self.client.close()

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

        response = await self.client.chat.completions.create(
            model=await self.get_model(),
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
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

        response = await self.client.chat.completions.create(
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

        choice = response.choices[0]

        if choice.finish_reason == "length":
            raise VLLMTruncatedError(
                f"generation hit max_tokens={max_tokens} "
                f"(finish_reason=length); lower Extraction.max_length "
                f"or raise max_tokens within --max-model-len"
            )

        content = choice.message.content
        if content is None:
            raise RuntimeError("vLLM returned an empty response")
        return content
