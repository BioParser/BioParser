"""Run a blocking function off the event loop without overlapping the next job.

Python cannot stop a thread. A time limit cancels the coroutine, and the thread
keeps running until the function returns. `call` waits for that return before it
raises `CancelledError`, and the queue waits for the coroutine before it takes
the next message. A timed-out parse can finish late. It cannot run beside the
next job in the same process.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

LOGGER = logging.getLogger(__name__)


async def call[**P, R](func: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """Run `func` in a thread. A cancellation waits for `func` to return."""
    task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await _finish(task)
        raise


async def _finish(task: asyncio.Task[object]) -> None:
    """Wait out the thread. Further cancellation does not abandon it."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        LOGGER.warning("blocking call finished after cancellation: %s", type(error).__name__)
