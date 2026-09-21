from redis.asyncio import Redis

from .models import JobState

DEFAULT_KEY_PREFIX = "bioparser:job:"


class RedisJobStateStore:
    """Redis-backed job state.

    Each job's state lives under its own key, `{key_prefix}{job_id}`, so
    distinct job IDs never share storage and can't interfere with each other.
    """

    def __init__(self, redis_url: str, *, key_prefix: str = DEFAULT_KEY_PREFIX) -> None:
        self._key_prefix = key_prefix
        self._redis: Redis = Redis.from_url(redis_url)

    def _key(self, job_id: str) -> str:
        return f"{self._key_prefix}{job_id}"

    async def aclose(self) -> None:
        await self._redis.aclose()

    async def create(self, state: JobState) -> None:
        await self._redis.set(self._key(state.job_id), state.model_dump_json())

    async def get(self, job_id: str) -> JobState | None:
        raw = await self._redis.get(self._key(job_id))
        if raw is None:
            return None
        return JobState.model_validate_json(raw)

    async def update(self, state: JobState) -> None:
        await self._redis.set(self._key(state.job_id), state.model_dump_json())
