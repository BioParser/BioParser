from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from .errors import (
    CorruptJobStateError,
    JobAlreadyExistsError,
    JobNotFoundError,
    JobStateConnectionError,
)
from .models import JobState

DEFAULT_KEY_PREFIX = "bioparser:job:"

#: Bounds how long a job's state stays in Redis after its last write.
#: Redis holds only a reference to extraction output, never the output
#: itself (see docs/architecture.md), so this only bounds how long a job
#: is *findable* by job_id -- it never deletes extraction results.
DEFAULT_TTL_SECONDS = 90 * 24 * 60 * 60  # 90 days

#: Bounds how long a single Redis operation (connect or command) may take
#: before giving up, so a slow-but-reachable Redis can't hang a caller.
DEFAULT_OPERATION_TIMEOUT_SECONDS = 2.0


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
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds
        self._redis: Redis = Redis.from_url(
            redis_url,
            socket_timeout=operation_timeout_seconds,  # Constructor parameter for the socket timeout in seconds. This is the maximum amount of time that a socket operation (connect, read, write) can take before timing out. Future caller (API/worker) can override this without touching the file.
            socket_connect_timeout=operation_timeout_seconds,  # Constructor parameter for the socket connect timeout in seconds. This is the maximum amount of time that a socket connection attempt can take before timing out. Future caller (API/worker) can override this without touching the file.
        )

    def _key(self, job_id: str) -> str:
        return f"{self._key_prefix}{job_id}"

    async def aclose(self) -> None:
        await self._redis.aclose()

    async def create(self, state: JobState) -> None:
        try:
            created = await self._redis.set(
                self._key(state.job_id),
                state.model_dump_json(),
                nx=True,
                ex=self._ttl_seconds,
            )
        except (RedisConnectionError, RedisTimeoutError) as exc:
            raise JobStateConnectionError(f"could not reach Redis: {type(exc).__name__}") from exc
        if not created:
            raise JobAlreadyExistsError(f"job {state.job_id!r} already has stored state")

    async def get(self, job_id: str) -> JobState | None:
        try:
            raw = await self._redis.get(self._key(job_id))
        except (RedisConnectionError, RedisTimeoutError) as exc:
            raise JobStateConnectionError(f"could not reach Redis: {type(exc).__name__}") from exc
        if raw is None:
            return None
        try:
            return JobState.model_validate_json(raw)
        except ValidationError as exc:
            raise CorruptJobStateError(f"stored state for job {job_id!r} is invalid") from exc

    async def update(self, state: JobState) -> None:
        try:
            updated = await self._redis.set(
                self._key(state.job_id),
                state.model_dump_json(),
                xx=True,
                ex=self._ttl_seconds,
            )
        except (RedisConnectionError, RedisTimeoutError) as exc:
            raise JobStateConnectionError(f"could not reach Redis: {type(exc).__name__}") from exc
        if not updated:
            raise JobNotFoundError(f"no stored state for job {state.job_id!r}")
