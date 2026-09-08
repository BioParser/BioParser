import argparse
import asyncio

from bioparser.services.vllm import VLLMService


async def run_prompt(
    prompt: str,
    *,
    system_prompt: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
) -> str:
    service = VLLMService()
    try:
        await service.initialize()
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

    response = asyncio.run(
        run_prompt(
            args.prompt,
            system_prompt=args.system_prompt,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
    )
    print(response)


if __name__ == "__main__":
    main()
