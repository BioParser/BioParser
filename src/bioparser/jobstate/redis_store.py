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


class RedisJobStateStore:
    """Redis-backed job state.

    Each job's state lives under its own key, `{key_prefix}{job_id}`.
    """

    def __init__(self, redis_url: str, *, key_prefix: str = DEFAULT_KEY_PREFIX) -> None:
        self._key_prefix = key_prefix
        self._redis: Redis = Redis.from_url(redis_url)

    def _key(self, job_id: str) -> str:
        return f"{self._key_prefix}{job_id}"

    async def aclose(self) -> None:
        await self._redis.aclose()

    async def create(self, state: JobState) -> None:
        try:
            created = await self._redis.set(
                self._key(state.job_id), state.model_dump_json(), nx=True
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
                self._key(state.job_id), state.model_dump_json(), xx=True
            )
        except (RedisConnectionError, RedisTimeoutError) as exc:
            raise JobStateConnectionError(f"could not reach Redis: {type(exc).__name__}") from exc
        if not updated:
            raise JobNotFoundError(f"no stored state for job {state.job_id!r}")
