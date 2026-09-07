import asyncio

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from bioparser.config import get_settings


# TODO: expand and add logger
class VLLMService:
    def __init__(self) -> None:
        settings = get_settings()

        self.client = AsyncOpenAI(
            base_url=settings.vllm_base_url,
            api_key="EMPTY",
            timeout=settings.request_timeout_seconds,
        )

        self.model: str | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Discover the served model id"""

        async with self._lock:
            if self.model is not None:
                return

            response = await self.client.models.list()

            if not response.data:
                raise RuntimeError("vLLM exposes no models")

            self.model = response.data[0].id

    async def aclose(self) -> None:
        await self.client.close()

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str:

        if self.model is None:
            await self.initialize()

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
            model=self.model or "",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        content = response.choices[0].message.content

        if content is None:
            raise RuntimeError("vLLM returned an empty response")

        return content
