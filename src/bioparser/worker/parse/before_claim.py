"""MinerU check run after load and before claim on a parse delivery."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from bioparser.jobs import JobState
from bioparser.jobs.queue import JobDeferred
from bioparser.parser_names import MINERU_PARSER_NAME
from bioparser.services.mineru.client import MinerUClient

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
