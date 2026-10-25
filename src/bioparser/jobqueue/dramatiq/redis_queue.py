"""Redis-backed job queue (Dramatiq).

Delivery is at-least-once: a worker crash or handler exception can re-run a job.

Defaults:
- ``time_limit``: 10 minutes (600_000 ms). Cancels the handler. See ``_on_loop``.
  The process exits if the handler is still going after another limit, or if a
  reporting hook or shutdown runs past one limit.
- ``max_retries``: 3. Handler exceptions retry.
- ``min_backoff``: 15 seconds before the first retry, backing off exponentially.

One thread consumes the queue. ``asyncio.to_thread`` uses one more thread, so a
handler's blocking calls run in the order they were started. After a time limit
the queue waits for that thread before the next message.

Messages that leave the queue without a successful handler run are poisoned.
"""

import asyncio
import concurrent.futures
import logging
import os
import re
import sys
from collections.abc import Callable, Coroutine, Iterator, Mapping
from contextlib import contextmanager
from threading import Event, Lock, Thread
from typing import Any, TypeVar

import dramatiq
import redis
from dramatiq import Actor, Broker, Worker
from dramatiq.asyncio import EventLoopThread, get_event_loop_thread, set_event_loop_thread
from dramatiq.brokers.redis import RedisBroker
from dramatiq.common import compute_backoff, current_millis
from dramatiq.middleware import CurrentMessage
from dramatiq.middleware.time_limit import TimeLimit, TimeLimitExceeded
from pydantic import BaseModel, ValidationError

from ..errors import (
    JobDeferralExpired,
    JobDeferred,
    JobQueueConfigError,
    JobQueueError,
    JobTimeLimitExceeded,
    MalformedJob,
)
from ..protocol import JobHandler, JobQueue

LOGGER = logging.getLogger(__name__)

_R = TypeVar("_R")

# How often the worker thread surfaces a time-limit exception while it waits.
# The exception is injected and lands at the next bytecode, so a blocking wait
# has to return on a short interval.
_WAIT_POLL_S = 0.1

_DEFAULT_TIME_LIMIT_MS = 600_000
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_MIN_BACKOFF_MS = 15_000
_DEFAULT_BROKER_NAMESPACE = "bioparser"
# How long an empty-queue poll blocks before the consumer checks for shutdown.
# Dramatiq's name. The handler's limit is `_DEFAULT_TIME_LIMIT_MS`.
_DEFAULT_WORKER_TIMEOUT_MS = 1000
_DEFAULT_REDIS_TIMEOUT_S = 5.0
# A dependency outage sends a new message instead of using a retry, so a restart
# longer than the retry budget can still finish. The delay doubles from the
# minimum up to the cap. Past the deadline the queue stops sending.
_DEFER_MIN_DELAY_MS = 30_000
_DEFER_MAX_DELAY_MS = 300_000
_DEFER_DEADLINE_MS = 3_600_000
_OPTION_DEFERRED_SINCE = "deferred_since"
_OPTION_DEFERRALS = "deferrals"
# The same pattern Dramatiq checks when the actor is declared.
_QUEUE_NAME_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9._-]*")


class DramatiqJobQueue[T: BaseModel](JobQueue[T]):
    """Named Redis queue bound to one pydantic message model."""

    def __init__(
        self,
        *,
        name: str,
        model: type[T],
        broker: Broker,
        time_limit_ms: int = _DEFAULT_TIME_LIMIT_MS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        min_backoff_ms: int = _DEFAULT_MIN_BACKOFF_MS,
        worker_timeout_ms: int = _DEFAULT_WORKER_TIMEOUT_MS,
    ) -> None:
        _require_queue_name(name)
        if max_retries < 0:
            raise JobQueueConfigError("max_retries must be >= 0")
        if time_limit_ms <= 0:
            raise JobQueueConfigError("time_limit_ms must be > 0")
        if min_backoff_ms <= 0:
            raise JobQueueConfigError("min_backoff_ms must be > 0")
        if worker_timeout_ms <= 0:
            raise JobQueueConfigError("worker_timeout_ms must be > 0")
        if name in broker.actors:
            raise JobQueueConfigError(f"queue {name!r} is already registered on this broker")
        self._name = name
        self._model = model
        self._broker = broker
        self._max_retries = max_retries
        self._time_limit_ms = time_limit_ms
        self._worker_timeout_ms = worker_timeout_ms
        self._handler: JobHandler[T] | None = None
        self._consuming = Lock()
        try:
            _ensure_current_message(broker)
            _start_time_limit(broker)
            self._actor = self._register_actor(
                time_limit_ms=time_limit_ms,
                max_retries=max_retries,
                min_backoff_ms=min_backoff_ms,
            )
        except ValueError as exc:
            raise JobQueueConfigError(str(exc)) from exc

    @classmethod
    def from_redis(
        cls,
        url: str,
        *,
        name: str,
        model: type[T],
        timeout_s: float = _DEFAULT_REDIS_TIMEOUT_S,
        namespace: str = _DEFAULT_BROKER_NAMESPACE,
        time_limit_ms: int = _DEFAULT_TIME_LIMIT_MS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        min_backoff_ms: int = _DEFAULT_MIN_BACKOFF_MS,
        worker_timeout_ms: int = _DEFAULT_WORKER_TIMEOUT_MS,
    ) -> "DramatiqJobQueue[T]":
        """A queue backed by Redis. The broker stays inside this backend."""
        return cls(
            name=name,
            model=model,
            broker=_create_redis_broker(url, timeout_s=timeout_s, namespace=namespace),
            time_limit_ms=time_limit_ms,
            max_retries=max_retries,
            min_backoff_ms=min_backoff_ms,
            worker_timeout_ms=worker_timeout_ms,
        )

    async def submit(self, message: T) -> None:
        # Dramatiq's producer is blocking, so the thread hop stays inside this backend.
        await asyncio.to_thread(self._submit, message)

    def _submit(self, message: T) -> None:
        payload = message.model_dump(mode="json")
        try:
            self._actor.send(payload)
        except redis.RedisError as exc:
            raise JobQueueError(f"could not enqueue on queue {self._name!r}") from exc

    def consume(
        self,
        handler: JobHandler[T],
        *,
        until_empty: bool = False,
        stop: Event | None = None,
    ) -> None:
        with self._bind(handler), self._event_loop(handler):
            worker = Worker(
                self._broker,
                queues={self._name},
                worker_timeout=self._worker_timeout_ms,
                worker_threads=1,
            )
            worker.start()
            try:
                if until_empty:
                    try:
                        self._broker.join(self._name)
                    except redis.RedisError as exc:
                        raise JobQueueError(f"could not drain queue {self._name!r}") from exc
                else:
                    (stop if stop is not None else Event()).wait()
            finally:
                worker.stop()

    @contextmanager
    def _event_loop(self, handler: JobHandler[T]) -> Iterator[None]:
        """Run every handler on one loop, then close the handler on it."""
        loop_thread = EventLoopThread(LOGGER)
        loop_thread.start(timeout=5.0)
        set_event_loop_thread(loop_thread)
        # One thread, so a handler's blocking calls run in submission order. The join
        # after a time limit queues behind them.
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"{self._name}-blocking"
        )
        try:
            _use_blocking_executor(loop_thread.loop, executor)
            yield
        finally:
            try:
                self._on_loop(handler.aclose(), bound_s=self._hook_bound_s())
            except Exception:
                LOGGER.exception("handler aclose failed on queue %s", self._name)
            finally:
                executor.shutdown(wait=True)
                loop_thread.stop()  # type: ignore[no-untyped-call]
                set_event_loop_thread(None)

    def _hook_bound_s(self) -> float:
        """How long a reporting hook or shutdown may run before the process exits."""
        return self._time_limit_ms / 1000

    def _handler_bound_s(self) -> float:
        """How long the handler may run before the process exits.

        Dramatiq cancels it at the time limit. The same limit again is for the
        blocking thread to finish, including cleanup started while cancelling.
        """
        return 2 * self._time_limit_ms / 1000

    def _on_loop(self, coroutine: Coroutine[Any, Any, _R], *, bound_s: float) -> _R:
        """Run `coroutine` on the consumer loop. Return when this worker can take another message.

        `bound_s` is how long this call may sit here. Past that, the process exits:
        a stuck thread cannot be interrupted, and a live worker would keep reporting healthy.
        """
        loop_thread = get_event_loop_thread()
        if loop_thread is None:
            coroutine.close()
            raise JobQueueError("the queue is not consuming")
        # The worker waits on this, because cancelling the future completes it
        # before the coroutine has unwound.
        finished = Event()
        returned = Event()
        Thread(
            target=_exit_when_stuck,
            args=(returned, bound_s),
            name="bioparser-stuck-call",
            daemon=True,
        ).start()

        async def watched() -> _R:
            try:
                return await coroutine
            finally:
                finished.set()

        # Used instead of EventLoopThread.run_coroutine, which returns one second after
        # the time limit and would let this worker take the next message while a blocking
        # call was still running.
        future = asyncio.run_coroutine_threadsafe(watched(), loop_thread.loop)
        try:
            try:
                return _wait(future)
            except TimeLimitExceeded:
                # The handler sees CancelledError. A call already on the blocking
                # thread keeps running; the next one waits behind it.
                future.cancel()
                _wait_for(finished)
                _join_blocking(loop_thread.loop)
                raise
        finally:
            returned.set()

    @contextmanager
    def _bind(self, handler: JobHandler[T]) -> Iterator[None]:
        """Hold the queue's single consumer slot: a second consumer would unbind this one."""
        if not self._consuming.acquire(blocking=False):
            raise JobQueueError(f"queue {self._name!r} is already being consumed")
        self._handler = handler
        try:
            yield
        finally:
            self._handler = None
            self._consuming.release()

    def _register_actor(
        self, *, time_limit_ms: int, max_retries: int, min_backoff_ms: int
    ) -> Actor[[object], None]:
        queue = self

        @dramatiq.actor(
            broker=self._broker,
            actor_name=self._name,
            queue_name=self._name,
            max_retries=max_retries,
            min_backoff=min_backoff_ms,
            time_limit=time_limit_ms,
            throws=(MalformedJob,),
        )
        def dispatch(payload: object) -> None:
            try:
                queue._dispatch(payload)
            except JobDeferred:
                # A new message, so the wait uses none of this delivery's retries.
                queue._defer(payload)
            except MalformedJob as exc:
                queue._end_deferral()
                LOGGER.warning("malformed message on queue %s: %s", queue._name, exc)
                queue._notify_malformed(payload)
                raise
            except (Exception, TimeLimitExceeded) as exc:
                # A retry enqueues this same message. Drop the outage clock first.
                queue._end_deferral()
                if queue._is_last_retry():
                    reported = JobTimeLimitExceeded() if isinstance(exc, TimeLimitExceeded) else exc
                    queue._notify_failed(payload, reported)
                raise
            else:
                queue._end_deferral()

        return dispatch

    def _end_deferral(self) -> None:
        """This delivery was not deferred, so its outage clock is over.

        A retry enqueues the same message. Without these options the next
        deferral starts a new hour.
        """
        current = CurrentMessage.get_current_message()
        if current is None:
            return
        current.options.pop(_OPTION_DEFERRED_SINCE, None)
        current.options.pop(_OPTION_DEFERRALS, None)

    def _defer(self, payload: object) -> None:
        current = CurrentMessage.get_current_message()
        options = current.options if current is not None else {}
        now = current_millis()
        since = _option_int(options, _OPTION_DEFERRED_SINCE)
        if since is not None and now - since >= _DEFER_DEADLINE_MS:
            LOGGER.error(
                "dependency is still unavailable on queue %s. Not sending it again",
                self._name,
            )
            self._notify_failed(payload, JobDeferralExpired())
            return
        deferrals = _option_int(options, _OPTION_DEFERRALS)
        if deferrals is None or deferrals < 0:
            deferrals = 0
        deferrals, delay_ms = compute_backoff(
            deferrals,
            factor=_DEFER_MIN_DELAY_MS,
            max_backoff=_DEFER_MAX_DELAY_MS,
        )
        resend_options: dict[str, Any] = {
            _OPTION_DEFERRED_SINCE: now if since is None else since,
            _OPTION_DEFERRALS: deferrals,
        }
        try:
            self._actor.send_with_options(args=(payload,), delay=delay_ms, **resend_options)
        except redis.RedisError as exc:
            raise JobQueueError(f"could not defer a message on queue {self._name!r}") from exc

    def _dispatch(self, payload: object) -> None:
        if not isinstance(payload, dict):
            raise MalformedJob(f"payload on queue {self._name} is not an object")
        try:
            message = self._model.model_validate(payload)
        except ValidationError as exc:
            raise MalformedJob(
                f"payload on queue {self._name} failed validation: {_validation_summary(exc)}"
            ) from exc
        handler = self._handler
        if handler is None:
            LOGGER.error("no handler set on queue %s; message will be retried", self._name)
            raise JobQueueError(f"no handler set on queue {self._name}")
        self._on_loop(handler.handle(message), bound_s=self._handler_bound_s())

    def _is_last_retry(self) -> bool:
        current = CurrentMessage.get_current_message()
        if current is None:
            return True
        retries = int(current.options.get("retries", 0))
        return retries >= self._max_retries

    def _notify_malformed(self, payload: object) -> None:
        handler = self._handler
        if handler is None:
            return
        try:
            self._on_loop(handler.on_malformed(payload), bound_s=self._hook_bound_s())
        except Exception:
            LOGGER.exception("on_malformed hook failed on queue %s", self._name)

    def _notify_failed(self, payload: object, exc: BaseException) -> None:
        handler = self._handler
        if handler is None:
            return
        try:
            message = self._model.model_validate(payload)
        except ValidationError:
            self._notify_malformed(payload)
            return
        try:
            self._on_loop(handler.on_failed(message, exc), bound_s=self._hook_bound_s())
        except Exception:
            LOGGER.exception("on_failed hook failed on queue %s", self._name)


class DramatiqJobSubmitter:
    """Sends to a queue that another process consumes.

    That process owns retries and the time limit. This one registers no actor,
    so it cannot be started as a consumer.
    """

    def __init__(self, *, name: str, broker: Broker) -> None:
        _require_queue_name(name)
        self._name = name
        self._broker = broker
        broker.declare_queue(name)

    @classmethod
    def from_redis(
        cls,
        url: str,
        *,
        name: str,
        timeout_s: float = _DEFAULT_REDIS_TIMEOUT_S,
        namespace: str = _DEFAULT_BROKER_NAMESPACE,
    ) -> "DramatiqJobSubmitter":
        """A submitter backed by Redis. The broker stays inside this object."""
        return cls(
            name=name,
            broker=_create_redis_broker(url, timeout_s=timeout_s, namespace=namespace),
        )

    async def submit(self, message: BaseModel) -> None:
        await asyncio.to_thread(self._submit, message)

    def _submit(self, message: BaseModel) -> None:
        payload = message.model_dump(mode="json")
        # The consumer registers an actor whose name is the queue name.
        envelope: dramatiq.Message[None] = dramatiq.Message(
            queue_name=self._name,
            actor_name=self._name,
            args=(payload,),
            kwargs={},
            options={},
        )
        try:
            self._broker.enqueue(envelope)
        except redis.RedisError as exc:
            raise JobQueueError(f"could not enqueue on queue {self._name!r}") from exc


def _require_queue_name(name: str) -> None:
    if not _QUEUE_NAME_RE.fullmatch(name):
        raise JobQueueConfigError(
            "queue name must start with a letter or underscore, "
            "followed by letters, digits, dots, dashes, or underscores"
        )


def _create_redis_broker(
    url: str,
    *,
    timeout_s: float = _DEFAULT_REDIS_TIMEOUT_S,
    namespace: str = _DEFAULT_BROKER_NAMESPACE,
) -> RedisBroker:
    """The Redis broker for one queue. `from_redis` builds a new one on each call."""
    if timeout_s <= 0:
        raise JobQueueConfigError("timeout_s must be > 0")
    try:
        client = redis.Redis.from_url(
            url,
            socket_timeout=timeout_s,
            socket_connect_timeout=timeout_s,
        )
    except ValueError as exc:
        # The ValueError text can hold part of the URL: a password with an
        # unescaped "/", "#" or "?" ends up in redis-py's port error.
        raise JobQueueConfigError("invalid Redis URL") from exc
    return RedisBroker(client=client, namespace=namespace)  # type: ignore[no-untyped-call]


def _ensure_current_message(broker: Broker) -> None:
    if any(isinstance(middleware, CurrentMessage) for middleware in broker.middleware):
        return
    broker.add_middleware(CurrentMessage())


def _start_time_limit(broker: Broker) -> None:
    """Start the broker's TimeLimit timer, once.

    Dramatiq's command line starts it by emitting `process_boot`. A `Worker` built here is
    not run by that command line, so the timer thread is started directly. `Thread.start`
    raises the second time, and `process_boot` calls it unconditionally, so a later boot
    finds the thread already running and leaves it.

    The timer raises `TimeLimitExceeded` in the worker thread waiting in `_on_loop`.
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


def _option_int(options: Mapping[str, object], key: str) -> int | None:
    value = options.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _validation_summary(exc: ValidationError) -> str:
    """Field paths and error types only: rejected values never reach the logs."""
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['type']}"
        for error in exc.errors(include_url=False, include_input=False)
    )


def _use_blocking_executor(
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


def _exit_when_stuck(returned: Event, bound_s: float) -> None:
    if returned.wait(timeout=bound_s):
        return
    message = f"a queue call was still running {bound_s:.0f} seconds after it started; exiting"
    LOGGER.critical(message)
    sys.stderr.write(message + "\n")
    sys.stderr.flush()
    os._exit(1)


def _wait_for(event: Event) -> None:
    """Block until `event` is set. A time-limit exception during the wait is ignored."""
    while not event.is_set():
        try:
            event.wait(timeout=_WAIT_POLL_S)
        except TimeLimitExceeded:
            continue


def _join_blocking(loop: asyncio.AbstractEventLoop) -> None:
    """Wait until the one blocking thread is idle.

    This call is queued behind the handler's, and the thread runs them in order,
    so it returns only after those calls have. A time-limit exception during the
    wait is ignored. A call that never returns is ended by `_exit_when_stuck`.
    """
    idle = asyncio.run_coroutine_threadsafe(asyncio.to_thread(_idle), loop)
    while True:
        try:
            _wait(idle)
            return
        except TimeLimitExceeded:
            continue


def _idle() -> None:
    return None


def _wait[R](future: concurrent.futures.Future[R]) -> R:
    """Block until `future` finishes. A time-limit exception can land between polls."""
    while True:
        try:
            return future.result(timeout=_WAIT_POLL_S)
        except concurrent.futures.TimeoutError:
            if future.done():
                return future.result(timeout=0)
