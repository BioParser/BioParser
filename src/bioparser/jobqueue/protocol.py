from threading import Event
from typing import Protocol

from pydantic import BaseModel


class JobHandler[T: BaseModel](Protocol):
    """Consumer of one queue's messages.

    Every method runs on the consumer's one event loop. Subclass to inherit the
    reporting hooks as no-ops.
    """

    async def handle(self, message: T) -> None:
        """Process one message. Raising retries it up to the queue's retry limit.

        Raise `JobDeferred` instead when the job was not changed and should run
        later. That delivery is acknowledged and does not use a retry. The queue
        sends it again with a longer delay until a deadline, then calls
        `on_failed` with `JobDeferralExpired` and does not send it again.
        Any other result drops the deferral clock on this message, so a retry
        is not charged to the same outage.

        When the time limit passes, this coroutine is cancelled and sees
        `asyncio.CancelledError` at its next await. `asyncio.to_thread` keeps running;
        the queue joins that thread before the next message. The attempt is retried
        like any other failure.
        """
        ...

    async def on_malformed(self, payload: object) -> None:
        """Report a payload that failed validation. It is poisoned, not retried."""
        return

    async def on_failed(self, message: T, exc: BaseException) -> None:
        """Report a message that failed its final attempt. It is poisoned.

        `exc` is `JobTimeLimitExceeded` when the last attempt ran out of time.
        """
        return

    async def aclose(self) -> None:
        """Release what the handler holds. The queue calls this once, after the last message."""
        return


class JobSubmitter[T: BaseModel](Protocol):
    """Sends typed messages. Another process consumes them."""

    async def submit(self, message: T) -> None:
        """Enqueue a message."""
        ...


class JobConsumer[T: BaseModel](Protocol):
    """Consumes typed messages from one queue."""

    def consume(
        self,
        handler: JobHandler[T],
        *,
        until_empty: bool = False,
        stop: Event | None = None,
    ) -> None:
        """Deliver each message to handler.

        Blocks until ``stop`` is set. If stop is omitted, waits until the
        process is interrupted. If until_empty is True, process queued
        messages and return when the queue is empty.
        """
        ...
