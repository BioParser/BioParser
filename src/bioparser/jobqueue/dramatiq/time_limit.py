"""A time-limit timer that raises `JobTimeLimitExceeded` instead of Dramatiq's own exception.

Dramatiq's TimeLimit middleware enforces the limit by injecting an exception into the thread
that runs the handler. The injection lands inside the handler's own code, so the exception
cannot be translated afterwards: the type injected has to be the one handlers catch. The
middleware calls three things on its `manager`, and this class provides them.
"""

import logging
import threading
from threading import Thread
from time import monotonic, sleep

from dramatiq import Broker
from dramatiq.middleware import TimeLimit
from dramatiq.middleware.threading import raise_thread_exception

from ..errors import JobQueueConfigError, JobTimeLimitExceeded

LOGGER = logging.getLogger(__name__)


class _DeadlineThread(Thread):
    """Checks deadlines on an interval and raises JobTimeLimitExceeded in late threads."""

    def __init__(self, interval_s: float) -> None:
        super().__init__(daemon=True)
        self.interval = interval_s
        self._deadlines: dict[int, float] = {}
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._start_requested = False

    def start(self) -> None:
        """Start the timer once. Dramatiq's `process_boot` calls this again and must not fail."""
        with self._start_lock:
            if self._start_requested:
                return
            self._start_requested = True
        super().start()

    def add_timeout(self, thread_id: int, ttl_ms: float) -> None:
        with self._lock:
            self._deadlines[thread_id] = monotonic() + ttl_ms / 1000

    def remove_timeout(self, thread_id: int) -> None:
        with self._lock:
            self._deadlines.pop(thread_id, None)

    def run(self) -> None:
        while True:
            try:
                self._expire()
            except Exception:
                LOGGER.exception("unhandled error while checking time limits")
            sleep(self.interval)

    def _expire(self) -> None:
        now = monotonic()
        # Clear under the lock, inject after releasing it. Same gap as Dramatiq's timer:
        # the injection is only requested here and lands on the thread's next bytecode.
        # A handler blocked in a C call can still finish and take it on a later job.
        # If the thread is idle in its queue wait, the exception is not caught there and
        # that worker thread stops.
        late: list[int] = []
        with self._lock:
            for thread_id, deadline in self._deadlines.items():
                if now >= deadline:
                    late.append(thread_id)
            for thread_id in late:
                del self._deadlines[thread_id]
        for thread_id in late:
            LOGGER.warning("time limit exceeded. Raising in worker thread %r", thread_id)
            raise_thread_exception(thread_id, JobTimeLimitExceeded)  # type: ignore[no-untyped-call]


def use_job_time_limit(broker: Broker) -> None:
    """Make the broker's TimeLimit middleware raise JobTimeLimitExceeded, and start it.

    Dramatiq's own command line starts the timer by emitting `process_boot`. A `Worker` built
    here is not run by that command line, so the thread is started directly. Safe to repeat.
    """
    for middleware in broker.middleware:
        if not isinstance(middleware, TimeLimit):
            continue
        manager = middleware.manager
        if isinstance(manager, _DeadlineThread):
            break
        if not isinstance(manager, Thread) or manager.is_alive():
            raise JobQueueConfigError(
                "the broker's time limit is already running or is not thread based"
            )
        ours = _DeadlineThread(getattr(manager, "interval", 1.0))
        middleware.manager = ours  # type: ignore[assignment]
        ours.start()
        break
    else:
        raise JobQueueConfigError("the broker has no TimeLimit middleware to enforce limits")
