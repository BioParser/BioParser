import asyncio
import hashlib
import os
import uuid
from collections.abc import AsyncGenerator

import pytest
from redis.asyncio import Redis

from bioparser.jobstate import (
    CorruptJobStateError,
    JobAlreadyExistsError,
    JobNotFoundError,
    JobState,
    JobStateConnectionError,
    RedisJobStateStore,
    TerminalJobStateError,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

TEST_REDIS_URL = os.environ.get("BIOPARSER_TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _queued(job_id: str, **overrides: object) -> JobState:
    fields: dict[str, object] = {
        "job_id": job_id,
        "document_id": hashlib.sha256(job_id.encode()).hexdigest(),  # 64 hex chars, valid sha256
        "status": "queued",
        "input_artifact_ref": f"artifacts/{job_id}/input.pdf",
    }
    fields.update(overrides)
    return JobState.model_validate(fields)


@pytest.fixture
async def store() -> AsyncGenerator[RedisJobStateStore]:
    # A unique key prefix per test isolates it without needing a shared
    # FLUSHDB, so this file can safely run against a Redis instance other
    # tests/developers are also using.
    prefix = f"bioparser:test:{uuid.uuid4()}:"
    instance = RedisJobStateStore(TEST_REDIS_URL, key_prefix=prefix, ttl_seconds=60)
    try:
        await instance._redis.ping()
    except Exception as exc:  # noqa: BLE001 - any connection failure means "skip"
        await instance.aclose()
        pytest.skip(f"no Redis available at {TEST_REDIS_URL}: {exc}")
    try:
        yield instance
    finally:
        await instance.aclose()


async def test_create_then_get_round_trips(store: RedisJobStateStore) -> None:
    state = _queued(str(uuid.uuid4()))
    await store.create(state)

    fetched = await store.get(state.job_id)

    assert fetched == state


async def test_get_unknown_job_returns_none(store: RedisJobStateStore) -> None:
    assert await store.get(str(uuid.uuid4())) is None


async def test_create_rejects_duplicate_job_id(store: RedisJobStateStore) -> None:
    state = _queued(str(uuid.uuid4()))
    await store.create(state)

    with pytest.raises(JobAlreadyExistsError):
        await store.create(state)


async def test_update_replaces_stored_state(store: RedisJobStateStore) -> None:
    state = _queued(str(uuid.uuid4()))
    await store.create(state)

    running = state.with_changes(status="running")
    await store.update(running)
    succeeded = running.with_changes(
        status="succeeded",
        output_artifact_ref=f"artifacts/{state.job_id}/output.json",
    )
    await store.update(succeeded)

    assert await store.get(state.job_id) == succeeded


async def test_update_unknown_job_raises(store: RedisJobStateStore) -> None:
    state = _queued(str(uuid.uuid4()))

    with pytest.raises(JobNotFoundError):
        await store.update(state)


async def test_update_refuses_to_reopen_a_finished_job(store: RedisJobStateStore) -> None:
    """A lagged worker must not drag a terminal job back to a live status."""
    state = _queued(str(uuid.uuid4()))
    await store.create(state)
    stale = state.with_changes(status="running")  # the view a lagged worker still holds
    succeeded = stale.with_changes(
        status="succeeded",
        output_artifact_ref=f"artifacts/{state.job_id}/output.json",
    )
    await store.update(succeeded)

    with pytest.raises(TerminalJobStateError):
        await store.update(stale)

    assert await store.get(state.job_id) == succeeded


async def test_update_allows_a_terminal_state_over_a_terminal_state(
    store: RedisJobStateStore,
) -> None:
    """Only non-terminal writes are refused: a retry re-reporting its own result is fine."""
    state = _queued(str(uuid.uuid4()))
    await store.create(state)
    failed = state.with_changes(status="failed", error={"code": "parse_failed"})
    await store.update(failed)

    await store.update(failed)
    succeeded = state.with_changes(
        status="succeeded",
        output_artifact_ref=f"artifacts/{state.job_id}/output.json",
    )
    await store.update(succeeded)

    assert await store.get(state.job_id) == succeeded


async def test_concurrent_jobs_remain_independent(store: RedisJobStateStore) -> None:
    job_ids = [str(uuid.uuid4()) for _ in range(10)]
    for job_id in job_ids:
        await store.create(_queued(job_id))

    async def bump_to_running(job_id: str) -> None:
        current = await store.get(job_id)
        assert current is not None
        await store.update(current.with_changes(status="running"))

    # Genuinely concurrent, not sequential -- these interleave on the event
    # loop, so this actually exercises the independence claim rather than
    # just "nothing clobbers anything when called one at a time."
    await asyncio.gather(*(bump_to_running(job_id) for job_id in job_ids))

    for job_id in job_ids:
        state = await store.get(job_id)
        assert state is not None
        assert state.status == "running"


async def test_corrupt_stored_state_raises(store: RedisJobStateStore) -> None:
    job_id = str(uuid.uuid4())
    raw_client: Redis = store._redis
    await raw_client.set(store._key(job_id), "not valid json state", ex=60)

    with pytest.raises(CorruptJobStateError):
        await store.get(job_id)


async def test_ttl_is_applied_on_create(store: RedisJobStateStore) -> None:
    # `store` already confirmed Redis is reachable; build a second instance
    # here just to use a short, easy-to-assert-on TTL for this one check.
    short_ttl_store = RedisJobStateStore(
        TEST_REDIS_URL, key_prefix=store._key_prefix, ttl_seconds=5
    )
    try:
        state = _queued(str(uuid.uuid4()))
        await short_ttl_store.create(state)

        ttl = await short_ttl_store._redis.ttl(short_ttl_store._key(state.job_id))

        assert 0 < ttl <= 5
    finally:
        await short_ttl_store.aclose()


async def test_unreachable_redis_raises_connection_error() -> None:
    # Deliberately not using the `store` fixture -- this test's entire point
    # is that Redis is NOT reachable, so it must not depend on a working one.
    instance = RedisJobStateStore("redis://localhost:1/0")
    try:
        with pytest.raises(JobStateConnectionError):
            await instance.create(_queued(str(uuid.uuid4())))
    finally:
        await instance.aclose()
