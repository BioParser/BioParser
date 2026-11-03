from threading import Event
from typing import Protocol

from pydantic import BaseModel


class JobHandler[T: BaseModel](Protocol):
    """Consumer of one queue's messages."""

    async def handle(self, message: T) -> None:
        """Process one message. Raising retries it up to the queue's retry limit.

        Raise `JobDeferred` instead when the job was not changed and should run
        later. That delivery is acknowledged and does not use a retry. The queue
        sends it again with a longer delay until a deadline, then calls
        `on_failed` with `JobDeferralExpired` and does not send it again.
        Any other result drops the deferral clock on this message, so a retry
        is not charged to the same outage.

        When the time limit passes, the queue cancels this coroutine. At its next
        await it sees `asyncio.CancelledError`. Raising from `handle` (including
        after catching that error) retries the attempt like any other failure.
        Returning after catching the cancellation counts the delivery as handled;
        the queue does not send the message again.

        If the queue's backstop passes while work for this delivery is still
        running, the delivery is a final failure: `on_failed` is called with
        `JobTimeLimitExceeded`, the message is not sent again, and the consumer
        process exits. That does not apply if `handle` already returned
        normally, including after catching the cancellation; then the delivery
        is handled and `on_failed` is not called even if blocking cleanup is
        still running when the backstop passes.
        """
        ...

    async def on_malformed(self, payload: object) -> None:
        """Report a payload that failed validation. It is poisoned, not retried."""
        return

    async def on_failed(self, message: T, exc: BaseException) -> None:
        """Report a message that failed its final attempt. It is poisoned.

        `exc` is `JobTimeLimitExceeded` when the attempt ran out of time or the
        backstop fired while work for the delivery was still running and
        `handle` had not already returned normally.
        """
        return

    async def aopen(self) -> None:
        """Prepare the handler on the consumer loop, once, before the first message."""
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
        """Open the handler, deliver each message, then close it.

        Blocks until ``stop`` is set. If stop is omitted, waits until the
        process is interrupted. If until_empty is True, process queued
        messages and return when the queue is empty. The handler is closed
        when this returns, including when this raises.
        """
        ...
