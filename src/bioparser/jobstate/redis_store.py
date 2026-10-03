import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar
from uuid import UUID

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from .errors import (
    CorruptJobStateError,
    JobAlreadyExistsError,
    JobNotFoundError,
    JobStateBackendError,
    JobStateConnectionError,
    JobStateError,
    StaleJobStateWriteError,
)
from .models import ALLOWED_TRANSITIONS, JobState

DEFAULT_TTL_SECONDS = 90 * 24 * 60 * 60  # 90 days
DEFAULT_KEY_PREFIX = "bioparser:job:"  # not jobstate?
DEFAULT_OPERATION_TIMEOUT_SECONDS = 2.0

_MAX_TIMEOUT_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 0.1

# Lua runs on the Redis server as one indivisible step, so no other client
# can write between the status check and the SET. A plain read-then-write
# from Python could not promise that.
# ARGV[3] is the incoming status.
# Returns: 1 written, 0 no such key, -1 refused (ALLOWED_TRANSITIONS does not list the
# incoming status for the stored one). A stored value that cannot be decoded, or has an
# unknown status, is overwritten: get() reports it as corrupt, and the writer holds
# the only valid view.
_TRANSITIONS = ", ".join(
    f"{status}={{{', '.join(f'{nxt}=true' for nxt in sorted(nexts))}}}"
    for status, nexts in ALLOWED_TRANSITIONS.items()
)
_UPDATE_SCRIPT = f"""
local allowed = {{{_TRANSITIONS}}}
local current = redis.call("get", KEYS[1])
if not current then
    return 0
end
local ok, stored = pcall(cjson.decode, current)
if ok and type(stored) == "table" then
    local next_statuses = allowed[stored["status"]]
    if next_statuses and not next_statuses[ARGV[3]] then
        return -1
    end
end
redis.call("set", KEYS[1], ARGV[1], "EX", ARGV[2])
return 1
"""

T = TypeVar(
    "T"
)  # Creates a placeholder for a generic type variable T, which can be used to indicate that a function returns a value of the same type as one of its arguments.


async def _call_with_redis_errors[T](
    operation: Callable[[], Awaitable[T]],
    *,
    retry_timeout: bool = True,
) -> T:
    """Run a Redis call, translating its errors into JobStateError subclasses.

    A RedisTimeoutError is retried by default (Redis may just be briefly
    busy loading); every other Redis error fails immediately, since
    retrying a connection refusal or a rejected command wouldn't help.
    retry_timeout=False disables the retry for callers where a timed-out
    write may have actually succeeded server-side, making a retry unsafe
    (see create() below).
    """
    attempts = _MAX_TIMEOUT_ATTEMPTS if retry_timeout else 1
    delay = _RETRY_BASE_DELAY_SECONDS
    for attempt in range(attempts):
        try:
            return await operation()
        except RedisTimeoutError as exc:
            if attempt == attempts - 1:
                raise JobStateConnectionError(
                    f"could not reach Redis: {type(exc).__name__}: {exc}"
                ) from exc
            await asyncio.sleep(delay)
            delay *= 2
        except RedisConnectionError as exc:
            raise JobStateConnectionError(
                f"could not reach Redis: {type(exc).__name__}: {exc}"
            ) from exc
        except RedisError as exc:
            raise JobStateBackendError(
                f"Redis rejected the command: {type(exc).__name__}: {exc}"
            ) from exc
    raise AssertionError(
        "unreachable: loop always returns or raises"
    )  # mypy can't see that the loop always returns or raises


class RedisJobStateStore:
    """Redis-backed job state.

    Each job's state lives under its own key, `{key_prefix}{job_id}`, as a
    JSON-encoded JobState. Every write refreshes the key's TTL.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        key_prefix: str = DEFAULT_KEY_PREFIX,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        operation_timeout_seconds: float = DEFAULT_OPERATION_TIMEOUT_SECONDS,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
        if operation_timeout_seconds <= 0:
            raise ValueError(
                f"operation_timeout_seconds must be positive, got {operation_timeout_seconds}"
            )
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds
        try:
            self._redis = Redis.from_url(
                redis_url,
                socket_timeout=operation_timeout_seconds,
                socket_connect_timeout=operation_timeout_seconds,
            )
        except ValueError as exc:
            raise JobStateError(f"invalid Redis URL: {exc}") from exc
        self._update_script = self._redis.register_script(_UPDATE_SCRIPT)

    def _key(self, job_id: UUID) -> str:
        return f"{self._key_prefix}{job_id}"

    async def aclose(self) -> None:
        await self._redis.aclose()

    async def create(self, state: JobState) -> None:
        created = await _call_with_redis_errors(
            lambda: self._redis.set(
                self._key(state.job_id),
                state.model_dump_json(),
                nx=True,
                ex=self._ttl_seconds,
            ),
            retry_timeout=False,
        )
        if not created:
            raise JobAlreadyExistsError(f"job {state.job_id!r} already has stored state")

    async def get(self, job_id: UUID) -> JobState | None:
        raw = await _call_with_redis_errors(lambda: self._redis.get(self._key(job_id)))
        if raw is None:
            return None
        try:
            return JobState.model_validate_json(raw)
        except ValidationError as exc:
            raise CorruptJobStateError(f"stored state for job {job_id!r} is invalid") from exc

    async def update(self, state: JobState) -> None:
        """Replace the stored state, unless the status change is not allowed.

        ALLOWED_TRANSITIONS lists what may follow each stored status. "done" and
        "failed" are final. Any other refused write can only be a stale or
        duplicate one: delivery is at-least-once, so a worker that lost its
        heartbeat long enough to have its message requeued is not stopped, and
        can wake up and write a view of the job that another worker has since
        moved past. Such a write is refused rather than applied.
        """
        result = await _call_with_redis_errors(
            lambda: self._update_script(
                keys=[self._key(state.job_id)],
                args=[
                    state.model_dump_json(),
                    self._ttl_seconds,
                    state.status,
                ],
            )
        )
        if result == 0:
            raise JobNotFoundError(f"no stored state for job {state.job_id!r}")
        if result == -1:
            raise StaleJobStateWriteError(
                f"job {state.job_id!r}: refusing to write status={state.status!r}. "
                "The stored status does not allow that transition"
            )
