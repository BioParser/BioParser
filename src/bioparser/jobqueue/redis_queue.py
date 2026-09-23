"""Redis-backed job queue (Dramatiq).

Delivery is at-least-once: a worker crash or handler exception can re-run a job.

Defaults:
- ``time_limit``: 10 minutes (600_000 ms). Dramatiq kills the actor after this.
- ``max_retries``: 3. Handler exceptions retry.
- ``min_backoff``: 15 seconds before the first retry, backing off exponentially.
- ``worker_threads``: 1, so handlers run serially. Raising it runs them concurrently.

Messages that leave the queue without a successful handler run are poisoned.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event, Lock

import dramatiq
import redis
from dramatiq import Actor, Broker, Worker
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import CurrentMessage
from dramatiq.middleware.time_limit import TimeLimitExceeded
from pydantic import BaseModel, ValidationError

from .errors import JobQueueConfigError, JobQueueError, MalformedJob
from .protocol import JobHandler, JobQueue

LOGGER = logging.getLogger(__name__)

DEFAULT_TIME_LIMIT_MS = 600_000
DEFAULT_MAX_RETRIES = 3
DEFAULT_MIN_BACKOFF_MS = 15_000
DEFAULT_BROKER_NAMESPACE = "bioparser"
DEFAULT_WORKER_TIMEOUT_MS = 1000
DEFAULT_WORKER_THREADS = 1
DEFAULT_REDIS_TIMEOUT_S = 5.0


def create_redis_broker(
    url: str,
    *,
    timeout_s: float = DEFAULT_REDIS_TIMEOUT_S,
    namespace: str = DEFAULT_BROKER_NAMESPACE,
) -> RedisBroker:
    """Redis broker shared by named queues in this process."""
    if timeout_s <= 0:
        raise JobQueueConfigError("timeout_s must be > 0")
    try:
        client = redis.Redis.from_url(
            url,
            socket_timeout=timeout_s,
            socket_connect_timeout=timeout_s,
        )
    except ValueError as exc:
        raise JobQueueConfigError(str(exc)) from exc
    return RedisBroker(client=client, namespace=namespace)  # type: ignore[no-untyped-call]


class RedisJobQueue[T: BaseModel](JobQueue[T]):
    """Named Redis queue bound to one pydantic message model."""

    def __init__(
        self,
        *,
        name: str,
        model: type[T],
        broker: Broker,
        time_limit_ms: int = DEFAULT_TIME_LIMIT_MS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        min_backoff_ms: int = DEFAULT_MIN_BACKOFF_MS,
        worker_threads: int = DEFAULT_WORKER_THREADS,
        worker_timeout_ms: int = DEFAULT_WORKER_TIMEOUT_MS,
    ) -> None:
        if not name.strip():
            raise JobQueueConfigError("queue name must be non-empty")
        if max_retries < 0:
            raise JobQueueConfigError("max_retries must be >= 0")
        if time_limit_ms <= 0:
            raise JobQueueConfigError("time_limit_ms must be > 0")
        if min_backoff_ms <= 0:
            raise JobQueueConfigError("min_backoff_ms must be > 0")
        if worker_threads < 1:
            raise JobQueueConfigError("worker_threads must be >= 1")
        if worker_timeout_ms <= 0:
            raise JobQueueConfigError("worker_timeout_ms must be > 0")
        if name in broker.actors:
            raise JobQueueConfigError(f"queue {name!r} is already registered on this broker")
        _ensure_current_message(broker)
        self._name = name
        self._model = model
        self._broker = broker
        self._max_retries = max_retries
        self._worker_threads = worker_threads
        self._worker_timeout_ms = worker_timeout_ms
        self._handler: JobHandler[T] | None = None
        self._consuming = Lock()
        try:
            self._actor: Actor[[object], None] = self._register_actor(
                time_limit_ms=time_limit_ms,
                max_retries=max_retries,
                min_backoff_ms=min_backoff_ms,
            )
        except ValueError as exc:
            raise JobQueueConfigError(str(exc)) from exc

    def submit(self, message: T) -> None:
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
        with self._bind(handler):
            worker = Worker(
                self._broker,
                queues={self._name},
                worker_timeout=self._worker_timeout_ms,
                worker_threads=self._worker_threads,
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
            except MalformedJob as exc:
                LOGGER.warning("malformed message on queue %s: %s", queue._name, exc)
                queue._notify_malformed(payload)
                raise
            except (Exception, TimeLimitExceeded) as exc:
                if queue._is_last_retry():
                    queue._notify_failed(payload, exc)
                raise

        return dispatch

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
        handler.handle(message)

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
            handler.on_malformed(payload)
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
            handler.on_failed(message, exc)
        except Exception:
            LOGGER.exception("on_failed hook failed on queue %s", self._name)


def _validation_summary(exc: ValidationError) -> str:
    """Field paths and error types only: rejected values never reach the logs."""
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['type']}"
        for error in exc.errors(include_url=False, include_input=False)
    )


def _ensure_current_message(broker: Broker) -> None:
    if any(isinstance(middleware, CurrentMessage) for middleware in broker.middleware):
        return
    broker.add_middleware(CurrentMessage())
