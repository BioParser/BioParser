"""Job query coverage. The Postgres tests need BIOPARSER_TEST_DATABASE_URL."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from bioparser.db import engine as db_engine
from bioparser.db import jobs as job_queries
from bioparser.db.engine import DatabaseError, async_database_url
from bioparser.db.migrate import sync_database_url, upgrade
from bioparser.jobstate import (
    CorruptJobStateError,
    JobNotFoundError,
    JobState,
    StaleJobStateWriteError,
)
from bioparser.jobstate.models import ALLOWED_TRANSITIONS


def test_async_url_selects_asyncpg() -> None:
    url = "postgresql://bioparser:s3cret@postgres:5432/bioparser"
    assert (
        async_database_url(url) == "postgresql+asyncpg://bioparser:s3cret@postgres:5432/bioparser"
    )


def test_async_url_rewrites_the_sync_driver() -> None:
    url = "postgresql+psycopg://bioparser@postgres/bioparser"
    assert async_database_url(url) == "postgresql+asyncpg://bioparser@postgres/bioparser"


def test_async_url_keeps_asyncpg() -> None:
    url = "postgresql+asyncpg://bioparser@postgres/bioparser"
    assert async_database_url(url) == url


def test_async_url_rejects_another_scheme() -> None:
    with pytest.raises(ValueError, match="postgresql"):
        async_database_url("redis://localhost/0")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _queued(job_id: uuid.UUID, **overrides: object) -> JobState:
    fields: dict[str, object] = {
        "job_id": job_id,
        "status": "queued",
        "parser": "default",
        "extractor": "default",
        "pdf_ref": str(uuid.uuid4()),
    }
    fields.update(overrides)
    return JobState.model_validate(fields)


_PATH_TO = {
    "queued": [],
    "parsing": ["parsing"],
    "parsed": ["parsing", "parsed"],
    "extracting": ["parsing", "parsed", "extracting"],
    "done": ["parsing", "parsed", "extracting", "done"],
    "failed": ["failed"],
}


async def _create(state: JobState) -> None:
    async with db_engine.transaction() as connection:
        await job_queries.create(connection, state)


async def _get(job_id: uuid.UUID) -> JobState | None:
    async with db_engine.transaction() as connection:
        return await job_queries.get(connection, job_id)


async def _update(state: JobState) -> None:
    async with db_engine.transaction() as connection:
        await job_queries.update(connection, state)


def _in_status(state: JobState, status: str) -> JobState:
    """A view of `state` with `status`, carrying exactly the fields that status requires."""
    fields: dict[str, object] = {"status": status, "parse_result_ref": None, "error_code": None}
    if status == "failed":
        fields["error_code"] = "parse_failed"
    elif status == "done":
        fields["parse_result_ref"] = str(uuid.uuid4())
        fields["extract_result_ref"] = str(uuid.uuid4())
    elif status in ("parsed", "extracting"):
        fields["parse_result_ref"] = str(uuid.uuid4())
    return state.with_changes(**fields)


def _create_database(admin_url: str, name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine.dispose()


def _drop_database(admin_url: str, name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(
            text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"),
            {"name": name},
        )
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    engine.dispose()


@pytest.fixture
async def database() -> AsyncIterator[str]:
    raw = os.environ.get("BIOPARSER_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("BIOPARSER_TEST_DATABASE_URL is not set")
    admin_url = (
        make_url(sync_database_url(raw))
        .set(database="postgres")
        .render_as_string(hide_password=False)
    )
    name = f"jobs_{uuid.uuid4().hex}"
    _create_database(admin_url, name)
    bare = (
        make_url(raw)
        .set(database=name)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    try:
        upgrade(bare)
        await db_engine.open(bare)
        yield bare
    finally:
        await db_engine.aclose()
        _drop_database(admin_url, name)


async def _advance_to(state: JobState, status: str) -> JobState:
    current = state
    for step in _PATH_TO[status]:
        current = _in_status(state, step)
        await _update(current)
    return current


@pytest.mark.integration
@pytest.mark.anyio
async def test_create_then_get_round_trips(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)

    assert await _get(state.job_id) == state


@pytest.mark.integration
@pytest.mark.anyio
async def test_get_unknown_job_returns_none(database: str) -> None:
    assert await _get(uuid.uuid4()) is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_create_rejects_duplicate_job_id(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)

    with pytest.raises(DatabaseError, match="Postgres query failed"):
        await _create(state)


@pytest.mark.integration
@pytest.mark.anyio
async def test_update_replaces_stored_state(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)
    running = state.with_changes(status="parsing")
    await _update(running)
    succeeded = running.with_changes(status="parsed", parse_result_ref=str(uuid.uuid4()))
    await _update(succeeded)

    assert await _get(state.job_id) == succeeded


@pytest.mark.integration
@pytest.mark.anyio
async def test_update_unknown_job_raises(database: str) -> None:
    with pytest.raises(JobNotFoundError):
        await _update(_queued(uuid.uuid4()))


@pytest.mark.integration
@pytest.mark.anyio
async def test_update_refuses_to_reopen_a_finished_job(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)
    stale = state.with_changes(status="parsing")
    failed = state.with_changes(status="failed", error_code="parse_failed")
    await _update(failed)

    with pytest.raises(StaleJobStateWriteError):
        await _update(stale)

    assert await _get(state.job_id) == failed


@pytest.mark.integration
@pytest.mark.anyio
async def test_update_never_changes_a_terminal_state(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)
    failed = state.with_changes(status="failed", error_code="parse_failed")
    await _update(failed)

    with pytest.raises(StaleJobStateWriteError):
        await _update(failed)
    with pytest.raises(StaleJobStateWriteError):
        await _update(failed.with_changes(error_code="timeout"))

    assert await _get(state.job_id) == failed


@pytest.mark.integration
@pytest.mark.anyio
async def test_update_refuses_failed_over_done(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)
    done = await _advance_to(state, "done")

    with pytest.raises(StaleJobStateWriteError):
        await _update(state.with_changes(status="failed", error_code="timeout"))

    assert await _get(state.job_id) == done


@pytest.mark.integration
@pytest.mark.anyio
async def test_update_allows_a_later_stage_over_parsed(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)
    parsed = await _advance_to(state, "parsed")
    onward = parsed.with_changes(status="extracting")
    await _update(onward)

    assert await _get(state.job_id) == onward


@pytest.mark.integration
@pytest.mark.anyio
async def test_update_refuses_to_move_parsed_back_to_parsing(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)
    stale = state.with_changes(status="parsing")
    parsed = await _advance_to(state, "parsed")

    with pytest.raises(StaleJobStateWriteError):
        await _update(stale)

    assert await _get(state.job_id) == parsed


@pytest.mark.integration
@pytest.mark.anyio
async def test_update_refuses_done_over_failed(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)
    failed = state.with_changes(status="failed", error_code="parse_failed")
    await _update(failed)
    done = state.with_changes(
        status="done",
        parse_result_ref=str(uuid.uuid4()),
        extract_result_ref=str(uuid.uuid4()),
    )

    with pytest.raises(StaleJobStateWriteError):
        await _update(done)

    assert await _get(state.job_id) == failed


@pytest.mark.integration
@pytest.mark.anyio
async def test_concurrent_jobs_remain_independent(database: str) -> None:
    import asyncio

    job_ids = [uuid.uuid4() for _ in range(10)]
    for job_id in job_ids:
        await _create(_queued(job_id))

    async def bump_to_running(job_id: uuid.UUID) -> None:
        current = await _get(job_id)
        assert current is not None
        await _update(current.with_changes(status="parsing"))

    await asyncio.gather(*(bump_to_running(job_id) for job_id in job_ids))

    for job_id in job_ids:
        state = await _get(job_id)
        assert state is not None
        assert state.status == "parsing"


@pytest.mark.integration
@pytest.mark.anyio
async def test_corrupt_stored_state_raises(database: str) -> None:
    job_id = uuid.uuid4()
    engine = create_engine(sync_database_url(database))
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO jobs (job_id, schema_version, status, parser, extractor, pdf_ref) "
                "VALUES (:job_id, 2, 'queued', 'default', 'default', :pdf_ref)"
            ),
            {"job_id": job_id, "pdf_ref": str(uuid.uuid4())},
        )
    engine.dispose()

    with pytest.raises(CorruptJobStateError):
        await _get(job_id)


@pytest.mark.integration
@pytest.mark.anyio
async def test_update_lets_the_database_stamp_updated_at(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)
    engine = create_engine(sync_database_url(database))
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE jobs DISABLE TRIGGER jobs_set_updated_at"))
        connection.execute(
            text("UPDATE jobs SET updated_at = '2000-01-01 00:00:00+00' WHERE job_id = :job_id"),
            {"job_id": state.job_id},
        )
        connection.execute(text("ALTER TABLE jobs ENABLE TRIGGER jobs_set_updated_at"))
    await _update(state.with_changes(status="parsing"))
    with engine.connect() as connection:
        updated_at = connection.execute(
            text("SELECT updated_at FROM jobs WHERE job_id = :job_id"),
            {"job_id": state.job_id},
        ).scalar_one()
    engine.dispose()

    assert isinstance(updated_at, datetime)
    assert updated_at.year > 2000


@pytest.mark.integration
@pytest.mark.anyio
async def test_unreachable_postgres_raises_database_error() -> None:
    await db_engine.aclose()
    await db_engine.open("postgresql://bioparser:s3cret@127.0.0.1:1/bioparser")
    try:
        with pytest.raises(DatabaseError) as raised:
            await _create(_queued(uuid.uuid4()))
        assert "s3cret" not in str(raised.value)
    finally:
        await db_engine.aclose()


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("incoming", list(ALLOWED_TRANSITIONS))
@pytest.mark.parametrize("stored", list(ALLOWED_TRANSITIONS))
async def test_update_follows_the_transition_map(database: str, stored: str, incoming: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)
    current = await _advance_to(state, stored)
    attempt = _in_status(state, incoming)

    if incoming in ALLOWED_TRANSITIONS[stored]:  # type: ignore[index]
        await _update(attempt)
        assert await _get(state.job_id) == attempt
    else:
        with pytest.raises(StaleJobStateWriteError):
            await _update(attempt)
        assert await _get(state.job_id) == current


@pytest.mark.integration
@pytest.mark.anyio
async def test_update_refuses_failed_over_parsed(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)
    parsed = await _advance_to(state, "parsed")

    with pytest.raises(StaleJobStateWriteError):
        await _update(state.with_changes(status="failed", error_code="timeout"))

    assert await _get(state.job_id) == parsed
