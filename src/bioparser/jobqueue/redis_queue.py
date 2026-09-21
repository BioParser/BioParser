"""Redis-backed job queue (Dramatiq).

Delivery is at-least-once: a worker crash or handler exception can re-run a job.

Defaults:
- ``time_limit``: 10 minutes (600_000 ms). Dramatiq kills the actor after this.
- ``max_retries``: 3. Handler exceptions retry.

Messages that leave the queue without a successful handler run are poisoned.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Event

import dramatiq
import redis
from dramatiq import Actor, Broker, Worker
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import CurrentMessage
from dramatiq.middleware.time_limit import TimeLimitExceeded
from pydantic import BaseModel, ValidationError

from .errors import JobQueueConfigError, JobQueueError
from .protocol import JobQueue

LOGGER = logging.getLogger(__name__)

DEFAULT_TIME_LIMIT_MS = 600_000
DEFAULT_MAX_RETRIES = 3
DEFAULT_BROKER_NAMESPACE = "bioparser"
DEFAULT_WORKER_TIMEOUT_MS = 1000
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
    client = redis.Redis.from_url(
        url,
        socket_timeout=timeout_s,
        socket_connect_timeout=timeout_s,
    )
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
        on_malformed: Callable[[dict[str, object]], None] | None = None,
        on_failed: Callable[[T, BaseException], None] | None = None,
    ) -> None:
        if not name.strip():
            raise JobQueueConfigError("queue name must be non-empty")
        if max_retries < 0:
            raise JobQueueConfigError("max_retries must be >= 0")
        if time_limit_ms <= 0:
            raise JobQueueConfigError("time_limit_ms must be > 0")
        if name in broker.actors:
            raise JobQueueConfigError(f"queue {name!r} is already registered on this broker")
        _ensure_current_message(broker)
        self._name = name
        self._model = model
        self._broker = broker
        self._max_retries = max_retries
        self._handler: Callable[[T], None] | None = None
        self._on_malformed = on_malformed
        self._on_failed = on_failed
        self._actor_name = name
        try:
            self._actor: Actor[[object], None] = self._register_actor(
                time_limit_ms=time_limit_ms, max_retries=max_retries
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
        handler: Callable[[T], None],
        *,
        until_empty: bool = False,
        stop: Event | None = None,
    ) -> None:
        self._handler = handler
        worker = Worker(
            self._broker,
            queues={self._name},
            worker_timeout=DEFAULT_WORKER_TIMEOUT_MS,
            worker_threads=1,
        )
        worker.start()
        try:
            if until_empty:
                self._broker.join(self._name)
            else:
                (stop if stop is not None else Event()).wait()
        finally:
            worker.stop()
            self._handler = None

    def _register_actor(self, *, time_limit_ms: int, max_retries: int) -> Actor[[object], None]:
        queue = self

        @dramatiq.actor(
            broker=self._broker,
            actor_name=self._actor_name,
            queue_name=self._name,
            max_retries=max_retries,
            time_limit=time_limit_ms,
        )
        def dispatch(payload: object) -> None:
            try:
                queue._dispatch(payload)
            except (Exception, TimeLimitExceeded) as exc:
                if queue._is_last_retry():
                    queue._notify_failed_from_payload(payload, exc)
                raise

        return dispatch

    def _dispatch(self, payload: object) -> None:
        if not isinstance(payload, dict):
            LOGGER.warning("malformed message on queue %s: payload is not an object", self._name)
            return
        try:
            message = self._model.model_validate(payload)
        except ValidationError:
            LOGGER.warning("malformed message on queue %s", self._name, exc_info=True)
            self._notify_malformed(payload)
            return
        handler = self._handler
        if handler is None:
            LOGGER.error("failed message on queue %s: no handler set", self._name)
            self._notify_failed(message, JobQueueError(f"no handler set on queue {self._name}"))
            return
        handler(message)

    def _is_last_retry(self) -> bool:
        current = CurrentMessage.get_current_message()
        if current is None:
            return True
        retries = int(current.options.get("retries", 0))
        return retries >= self._max_retries

    def _notify_failed_from_payload(self, payload: object, exc: BaseException) -> None:
        try:
            message = self._model.model_validate(payload)
        except ValidationError:
            if isinstance(payload, dict):
                self._notify_malformed(payload)
            return
        self._notify_failed(message, exc)

    def _notify_malformed(self, payload: dict[str, object]) -> None:
        if self._on_malformed is None:
            return
        try:
            self._on_malformed(payload)
        except Exception:
            LOGGER.exception("on_malformed hook failed on queue %s", self._name)

    def _notify_failed(self, message: T, exc: BaseException) -> None:
        if self._on_failed is None:
            return
        try:
            self._on_failed(message, exc)
        except Exception:
            LOGGER.exception("on_failed hook failed on queue %s", self._name)


def _ensure_current_message(broker: Broker) -> None:
    if any(isinstance(middleware, CurrentMessage) for middleware in broker.middleware):
        return
    broker.add_middleware(CurrentMessage())
