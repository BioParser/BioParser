import asyncio
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
    StaleJobStateWriteError,
)
from bioparser.jobstate.models import ALLOWED_TRANSITIONS

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

TEST_REDIS_URL = os.environ.get("BIOPARSER_TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _queued(job_id: uuid.UUID, **overrides: object) -> JobState:
    fields: dict[str, object] = {
        "job_id": job_id,
        "status": "queued",
        "parser": "default",
        "pdf_ref": str(uuid.uuid4()),
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


_PATH_TO = {
    "queued": [],
    "parsing": ["parsing"],
    "parsed": ["parsing", "parsed"],
    "extracting": ["parsing", "parsed", "extracting"],
    "done": ["parsing", "parsed", "extracting", "done"],
    "failed": ["failed"],
}


def _in_status(state: JobState, status: str) -> JobState:
    """A view of `state` with `status`, carrying exactly the fields that status requires."""
    fields: dict[str, object] = {"status": status, "parse_result_ref": None, "error": None}
    if status == "failed":
        fields["error"] = {"code": "parse_failed"}
    elif status in ("parsed", "extracting", "done"):
        fields["parse_result_ref"] = str(uuid.uuid4())
    return state.with_changes(**fields)


async def _advance_to(store: RedisJobStateStore, state: JobState, status: str) -> JobState:
    """Walk a stored queued job through legal steps to `status`. Returns the final state."""
    current = state
    for step in _PATH_TO[status]:
        current = _in_status(state, step)
        await store.update(current)
    return current


async def test_create_then_get_round_trips(store: RedisJobStateStore) -> None:
    state = _queued(uuid.uuid4())
    await store.create(state)

    fetched = await store.get(state.job_id)

    assert fetched == state


async def test_get_unknown_job_returns_none(store: RedisJobStateStore) -> None:
    assert await store.get(uuid.uuid4()) is None


async def test_create_rejects_duplicate_job_id(store: RedisJobStateStore) -> None:
    state = _queued(uuid.uuid4())
    await store.create(state)

    with pytest.raises(JobAlreadyExistsError):
        await store.create(state)


async def test_update_replaces_stored_state(store: RedisJobStateStore) -> None:
    state = _queued(uuid.uuid4())
    await store.create(state)

    running = state.with_changes(status="parsing")
    await store.update(running)
    succeeded = running.with_changes(
        status="parsed",
        parse_result_ref=str(uuid.uuid4()),
    )
    await store.update(succeeded)

    assert await store.get(state.job_id) == succeeded


async def test_update_unknown_job_raises(store: RedisJobStateStore) -> None:
    state = _queued(uuid.uuid4())

    with pytest.raises(JobNotFoundError):
        await store.update(state)


async def test_update_refuses_to_reopen_a_finished_job(store: RedisJobStateStore) -> None:
    """A lagged worker must not drag a terminal job back to a live status."""
    state = _queued(uuid.uuid4())
    await store.create(state)
    stale = state.with_changes(status="parsing")  # the view a lagged worker still holds
    failed = state.with_changes(status="failed", error={"code": "parse_failed"})
    await store.update(failed)

    with pytest.raises(StaleJobStateWriteError):
        await store.update(stale)

    assert await store.get(state.job_id) == failed


async def test_update_never_changes_a_terminal_state(store: RedisJobStateStore) -> None:
    """done and failed are final: no write replaces them, not even an identical one."""
    state = _queued(uuid.uuid4())
    await store.create(state)
    failed = state.with_changes(status="failed", error={"code": "parse_failed"})
    await store.update(failed)

    with pytest.raises(StaleJobStateWriteError):
        await store.update(failed)
    with pytest.raises(StaleJobStateWriteError):
        await store.update(failed.with_changes(error={"code": "timeout"}))

    assert await store.get(state.job_id) == failed


async def test_update_refuses_failed_over_done(store: RedisJobStateStore) -> None:
    state = _queued(uuid.uuid4())
    await store.create(state)
    done = await _advance_to(store, state, "done")
    failed = state.with_changes(status="failed", error={"code": "timeout"})

    with pytest.raises(StaleJobStateWriteError):
        await store.update(failed)

    assert await store.get(state.job_id) == done


async def test_update_allows_a_later_stage_over_parsed(store: RedisJobStateStore) -> None:
    state = _queued(uuid.uuid4())
    await store.create(state)
    parsed = await _advance_to(store, state, "parsed")
    onward = parsed.with_changes(status="extracting")
    await store.update(onward)

    assert await store.get(state.job_id) == onward


async def test_update_refuses_to_move_parsed_back_to_parsing(store: RedisJobStateStore) -> None:
    """A lagging worker's stale write must not undo a completed stage."""
    state = _queued(uuid.uuid4())
    await store.create(state)
    stale = state.with_changes(status="parsing")
    parsed = await _advance_to(store, state, "parsed")

    with pytest.raises(StaleJobStateWriteError):
        await store.update(stale)

    assert await store.get(state.job_id) == parsed


async def test_update_refuses_done_over_failed(store: RedisJobStateStore) -> None:
    state = _queued(uuid.uuid4())
    await store.create(state)
    failed = state.with_changes(status="failed", error={"code": "parse_failed"})
    await store.update(failed)
    done = state.with_changes(status="done", parse_result_ref=str(uuid.uuid4()))

    with pytest.raises(StaleJobStateWriteError):
        await store.update(done)

    assert await store.get(state.job_id) == failed


async def test_concurrent_jobs_remain_independent(store: RedisJobStateStore) -> None:
    job_ids = [uuid.uuid4() for _ in range(10)]
    for job_id in job_ids:
        await store.create(_queued(job_id))

    async def bump_to_running(job_id: uuid.UUID) -> None:
        current = await store.get(job_id)
        assert current is not None
        await store.update(current.with_changes(status="parsing"))

    # Genuinely concurrent, not sequential -- these interleave on the event
    # loop, so this actually exercises the independence claim rather than
    # just "nothing clobbers anything when called one at a time."
    await asyncio.gather(*(bump_to_running(job_id) for job_id in job_ids))

    for job_id in job_ids:
        state = await store.get(job_id)
        assert state is not None
        assert state.status == "parsing"


async def test_corrupt_stored_state_raises(store: RedisJobStateStore) -> None:
    job_id = uuid.uuid4()
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
        state = _queued(uuid.uuid4())
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
            await instance.create(_queued(uuid.uuid4()))
    finally:
        await instance.aclose()


@pytest.mark.parametrize("incoming", list(ALLOWED_TRANSITIONS))
@pytest.mark.parametrize("stored", list(ALLOWED_TRANSITIONS))
async def test_update_follows_the_transition_map(
    store: RedisJobStateStore, stored: str, incoming: str
) -> None:
    """Every stored/incoming pair: written if ALLOWED_TRANSITIONS lists it, refused otherwise."""
    state = _queued(uuid.uuid4())
    await store.create(state)
    current = await _advance_to(store, state, stored)
    attempt = _in_status(state, incoming)

    if incoming in ALLOWED_TRANSITIONS[stored]:  # type: ignore[index]
        await store.update(attempt)
        assert await store.get(state.job_id) == attempt
    else:
        with pytest.raises(StaleJobStateWriteError):
            await store.update(attempt)
        assert await store.get(state.job_id) == current


async def test_update_refuses_failed_over_parsed(store: RedisJobStateStore) -> None:
    """A duplicate delivery that fails late must not turn a parsed job into a failed one."""
    state = _queued(uuid.uuid4())
    await store.create(state)
    parsed = await _advance_to(store, state, "parsed")

    with pytest.raises(StaleJobStateWriteError):
        await store.update(state.with_changes(status="failed", error={"code": "timeout"}))

    assert await store.get(state.job_id) == parsed
