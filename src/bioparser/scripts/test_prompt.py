import argparse
import asyncio
import sys

from openai import APIConnectionError, APITimeoutError

from bioparser.services.vllm import VLLMService
from bioparser.services.vllm.config import get_settings


def vllm_unreachable_message(base_url: str) -> str:
    return (
        f"Could not connect to vLLM at {base_url}. "
        "The server may still be loading the model (first start can take several minutes). "
        "Wait until curl http://127.0.0.1:8000/health succeeds, then retry. "
        "If the host port is not 8000, set BIOPARSER_VLLM_BASE_URL."
    )


async def run_prompt(
    prompt: str,
    *,
    system_prompt: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
) -> str:
    service = VLLMService()
    try:
        await service.get_model()
        return await service.generate(
            prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    finally:
        await service.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a test prompt to the local vLLM service.")
    parser.add_argument("prompt")
    parser.add_argument("--system-prompt")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=1024)
    args = parser.parse_args()

    try:
        response = asyncio.run(
            run_prompt(
                args.prompt,
                system_prompt=args.system_prompt,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
            )
        )
    except (APIConnectionError, APITimeoutError):
        print(vllm_unreachable_message(get_settings().vllm_base_url), file=sys.stderr)
        raise SystemExit(1) from None

    print(response)


if __name__ == "__main__":
    main()
