"""Bounds on a queue's calls into its consumer loop.

A thread that will not return cannot be interrupted, and a live worker would keep
reporting healthy. Each call into the loop is watched. When its bound passes, the
call that was stuck is counted as a failed attempt and the process exits. All state
belongs to one `DeliveryGuard`, so queues in one process do not affect each other.
"""

import asyncio
import concurrent.futures
import logging
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Event, Lock, Timer

from dramatiq import Broker
from dramatiq.broker import MessageProxy
from dramatiq.middleware import Middleware
from dramatiq.middleware.time_limit import TimeLimit, TimeLimitExceeded

from ..errors import JobQueueConfigError, JobQueueError, JobTimeLimitExceeded

LOGGER = logging.getLogger(__name__)

# How often the worker thread checks for a time-limit exception while it waits.
# The exception is injected and lands at the next bytecode, so a blocking wait
# has to return on a short interval.
_WAIT_POLL_S = 0.1


class DeliveryGuard:
    """One queue's watchdog state."""

    def __init__(self, name: str) -> None:
        self.name = name
        # Set when a delivery's bound passes, so the exit middleware leaves once
        # Dramatiq has acked or rejected that delivery.
        self.exit_after_ack = Event()
        # Set once that ack or rejection has happened.
        self.recorded = Event()
        self.middleware = _ExitAfterRecordedAttempt(self)

    @contextmanager
    def watch(self, bound_s: float, *, count_attempt: bool) -> Iterator["Watch"]:
        """Watch one call into the loop.

        Past `bound_s` a delivery (`count_attempt`) is failed so Dramatiq records the
        retry, and the process then exits. Shutdown has no delivery to record, so it
        exits immediately.
        """
        watch = Watch(self, bound_s, count_attempt)
        timer = Timer(bound_s, watch.expire)
        timer.name = f"{self.name}-watchdog"
        timer.daemon = True
        timer.start()
        try:
            yield watch
        finally:
            watch.finish()
            timer.cancel()


class Watch:
    """One watched call. `stuck` is set when its own bound passes."""

    def __init__(self, guard: DeliveryGuard, bound_s: float, count_attempt: bool) -> None:
        self._guard = guard
        self._bound_s = bound_s
        self._count_attempt = count_attempt
        self._lock = Lock()
        self._finished = False
        self.stuck = Event()

    def finish(self) -> None:
        with self._lock:
            self._finished = True

    def expire(self) -> None:
        with self._lock:
            if self._finished:
                return
            if self._count_attempt:
                # Before `stuck`: the worker fails the delivery as soon as it sees `stuck`.
                self._guard.recorded.clear()
                self._guard.exit_after_ack.set()
            self.stuck.set()
        _report_stuck(self._bound_s, counted=self._count_attempt)
        if self._count_attempt and self._guard.recorded.wait(timeout=self._bound_s):
            return
        if self._count_attempt:
            _report_stuck(self._bound_s, counted=False)
        os._exit(1)

    def wait[R](
        self, future: concurrent.futures.Future[R], *, ignore_injected_limit: bool = False
    ) -> R:
        """Block until `future` finishes.

        Raises `JobTimeLimitExceeded` once this call's bound has passed. Dramatiq's
        `TimeLimitExceeded` is injected into this thread and passes through, unless
        `ignore_injected_limit` is set. Set it while cleaning up after that exception
        was already handled.
        """
        while True:
            try:
                done, _ = concurrent.futures.wait([future], timeout=_WAIT_POLL_S)
                if done:
                    return future.result()
                if self.stuck.is_set():
                    raise JobTimeLimitExceeded()
            except TimeLimitExceeded:
                if not ignore_injected_limit:
                    raise

    def join_blocking(self, loop: asyncio.AbstractEventLoop) -> None:
        """Wait until the one blocking thread is idle.

        This call is queued behind the handler's, and the thread runs them in order,
        so it returns only after those calls have. A call that never returns fails the
        delivery.
        """
        idle = asyncio.run_coroutine_threadsafe(asyncio.to_thread(_idle), loop)
        self.wait(idle, ignore_injected_limit=True)


class _ExitAfterRecordedAttempt(Middleware):
    """Exit once Dramatiq has stored the retry for a call that would not return."""

    def __init__(self, guard: DeliveryGuard) -> None:
        self._guard = guard

    def after_ack(self, broker: Broker, message: MessageProxy) -> None:
        """The retry is stored and the message is settled. Leave before the next one starts."""
        guard = self._guard
        if message.actor_name != guard.name or not guard.exit_after_ack.is_set():
            return
        guard.recorded.set()
        os._exit(1)
        # Reached only when the exit is patched, so a later delivery in this process can run.
        guard.exit_after_ack.clear()
        guard.recorded.clear()

    after_nack = after_ack


def start_time_limit(broker: Broker) -> None:
    """Start the broker's TimeLimit timer, once.

    Dramatiq's command line starts it by emitting `process_boot`. A `Worker` built by
    the queue is not run by that command line, so the timer thread is started directly.
    `Thread.start` raises the second time, and `process_boot` calls it unconditionally,
    so a later boot finds the thread already running and leaves it.

    The timer raises `TimeLimitExceeded` in the worker thread waiting in `Watch.wait`.
    """
    for middleware in broker.middleware:
        if isinstance(middleware, TimeLimit):
            manager = middleware.manager
            start = manager.start

            def start_once(
                manager: object = manager,
                start: Callable[[], None] = start,
            ) -> None:
                alive = getattr(manager, "is_alive", None)
                if callable(alive) and alive():
                    return
                start()

            manager.start = start_once  # type: ignore[method-assign]
            start_once()
            return
    raise JobQueueConfigError("the broker has no TimeLimit middleware to enforce limits")


def use_blocking_executor(
    loop: asyncio.AbstractEventLoop,
    executor: concurrent.futures.ThreadPoolExecutor,
) -> None:
    """Send ``asyncio.to_thread`` on `loop` through `executor`."""
    installed = Event()

    def install() -> None:
        loop.set_default_executor(executor)
        installed.set()

    loop.call_soon_threadsafe(install)
    if not installed.wait(timeout=5.0):
        raise JobQueueError("the consumer loop did not install its blocking thread")


def _idle() -> None:
    return None


def _report_stuck(bound_s: float, *, counted: bool) -> None:
    action = "recording the attempt and exiting" if counted else "exiting"
    message = f"a queue call was still running {bound_s:.0f} seconds after it started; {action}"
    LOGGER.critical(message)
    sys.stderr.write(message + "\n")
    sys.stderr.flush()
