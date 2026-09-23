from threading import Event
from typing import Protocol

from pydantic import BaseModel


class JobHandler[T: BaseModel](Protocol):
    """Consumer of one queue's messages.

    Callbacks run concurrently when the queue consumes on more than one
    thread. Subclass to inherit the reporting hooks as no-ops.
    """

    def handle(self, message: T) -> None:
        """Process one message. Raising retries it up to the queue's retry limit."""
        ...

    def on_malformed(self, payload: object) -> None:
        """Report a payload that failed validation. It is poisoned, not retried."""
        return

    def on_failed(self, message: T, exc: BaseException) -> None:
        """Report a message that failed its final attempt. It is poisoned."""
        return


class JobQueue[T: BaseModel](Protocol):
    """Replaceable queue for submitting and consuming typed messages."""

    def submit(self, message: T) -> None:
        """Enqueue a message.

        Blocks on queue I/O: call it through ``asyncio.to_thread`` from async code.
        """
        ...

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
