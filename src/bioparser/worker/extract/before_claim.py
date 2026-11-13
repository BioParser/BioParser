"""vLLM check run after load and before claim on an extract delivery."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from bioparser.jobs import JobState
from bioparser.jobs.queue import JobDeferred
from bioparser.services.vllm import VLLMService

LOGGER = logging.getLogger(__name__)


def vllm_before_claim(vllm: VLLMService | None) -> Callable[[JobState], Awaitable[None]]:
    async def check(state: JobState) -> None:
        if vllm is None or await vllm.ping():
            return
        LOGGER.warning("vLLM is not answering")
        raise JobDeferred("vLLM is not answering")

    return check
