"""Dependency checks run after load and before claim on a queue delivery."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from bioparser.jobs import JobState
from bioparser.jobs.queue import JobDeferred
from bioparser.parser_names import MINERU_PARSER_NAME
from bioparser.services.mineru.client import MinerUClient

# Annotation only. Importing the client loads openai, which the parser image
# does not install, and the parser worker imports this module.
if TYPE_CHECKING:
    from bioparser.services.vllm.vllm import VLLMService

LOGGER = logging.getLogger(__name__)


def mineru_before_claim(base_url: str) -> Callable[[JobState], Awaitable[None]]:
    async def check(state: JobState) -> None:
        if state.parser != MINERU_PARSER_NAME:
            return
        if await MinerUClient.ping(base_url):
            return
        LOGGER.warning("mineru-api is not answering")
        raise JobDeferred("mineru-api is not answering")

    return check


def vllm_before_claim(vllm: VLLMService | None) -> Callable[[JobState], Awaitable[None]]:
    async def check(state: JobState) -> None:
        if vllm is None or await vllm.ping():
            return
        LOGGER.warning("vLLM is not answering")
        raise JobDeferred("vLLM is not answering")

    return check
