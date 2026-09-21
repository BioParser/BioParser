from collections.abc import Callable
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class JobQueue(Protocol[T]):
    """Replaceable queue for submitting and consuming typed messages."""

    def submit(self, message: T) -> None:
        """Enqueue a message."""
        ...

    def consume(self, handler: Callable[[T], None], *, until_empty: bool = False) -> None:
        """Deliver each message to handler.

        Blocks until interrupted. If until_empty is True, process queued
        messages and return when the queue is empty.
        """
        ...
