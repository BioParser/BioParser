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
from bioparser.jobstate import CorruptJobStateError, JobState


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


@pytest.mark.anyio
async def test_open_keeps_the_same_url_and_rejects_a_different_one() -> None:
    await db_engine.aclose()
    await db_engine.open("postgresql://bioparser@127.0.0.1:1/one")
    try:
        await db_engine.open("postgres://bioparser@127.0.0.1:1/one")
        with pytest.raises(RuntimeError, match="different URL"):
            await db_engine.open("postgresql://bioparser@127.0.0.1:1/two")
    finally:
        await db_engine.aclose()


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


async def _create(state: JobState) -> None:
    async with db_engine.transaction() as connection:
        await job_queries.create(connection, state)


async def _get(job_id: uuid.UUID) -> JobState | None:
    async with db_engine.transaction() as connection:
        return await job_queries.get(connection, job_id)


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
async def test_claim_counts_a_start_and_record_parsed_keeps_the_count(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)
    artifact = str(uuid.uuid4())

    async with db_engine.transaction() as connection:
        assert (
            await job_queries.claim(
                connection,
                state.job_id,
                status="parsing",
                from_statuses=("queued", "parsing"),
                max_claims=2,
            )
            == "claimed"
        )
        assert await job_queries.record_parsed(connection, state.job_id, artifact)

    stored = await _get(state.job_id)
    assert stored is not None
    assert stored.status == "parsed"
    assert stored.parse_result_ref == artifact
    assert stored.parsing_claims == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_claim_at_the_cap_marks_the_job_failed(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)

    async with db_engine.transaction() as connection:
        assert (
            await job_queries.claim(
                connection,
                state.job_id,
                status="parsing",
                from_statuses=("queued", "parsing"),
                max_claims=1,
            )
            == "claimed"
        )
    async with db_engine.transaction() as connection:
        assert (
            await job_queries.claim(
                connection,
                state.job_id,
                status="parsing",
                from_statuses=("queued", "parsing"),
                max_claims=1,
            )
            == "exhausted"
        )

    stored = await _get(state.job_id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error_code == "internal_error"
    assert stored.parsing_claims == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_claim_at_the_cap_leaves_a_status_that_cannot_fail(database: str) -> None:
    state = _queued(uuid.uuid4(), status="parsed", parse_result_ref=str(uuid.uuid4()))
    await _create(state)
    engine = create_engine(sync_database_url(database))
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET extracting_claims = 1 WHERE job_id = :job_id"),
            {"job_id": state.job_id},
        )
    engine.dispose()

    async with db_engine.transaction() as connection:
        assert (
            await job_queries.claim(
                connection,
                state.job_id,
                status="extracting",
                from_statuses=("parsed", "extracting"),
                max_claims=1,
            )
            == "skipped"
        )

    stored = await _get(state.job_id)
    assert stored is not None
    assert stored.status == "parsed"
    assert stored.extracting_claims == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_record_parsed_leaves_a_later_stage_unchanged(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)
    async with db_engine.transaction() as connection:
        await job_queries.claim(
            connection,
            state.job_id,
            status="parsing",
            from_statuses=("queued", "parsing"),
            max_claims=2,
        )
        await job_queries.record_parsed(connection, state.job_id, str(uuid.uuid4()))
        assert not await job_queries.record_parsed(connection, state.job_id, str(uuid.uuid4()))
        assert (
            await job_queries.mark_failed(
                connection, state.job_id, "internal_error", from_statuses=("parsed", "extracting")
            )
            == "not_allowed"
        )

    stored = await _get(state.job_id)
    assert stored is not None
    assert stored.status == "parsed"


@pytest.mark.integration
@pytest.mark.anyio
async def test_record_done_and_mark_failed_follow_the_stage(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)
    parse_ref = str(uuid.uuid4())
    extract_ref = str(uuid.uuid4())
    async with db_engine.transaction() as connection:
        await job_queries.claim(
            connection,
            state.job_id,
            status="parsing",
            from_statuses=("queued", "parsing"),
            max_claims=2,
        )
        await job_queries.record_parsed(connection, state.job_id, parse_ref)
        assert (
            await job_queries.claim(
                connection,
                state.job_id,
                status="extracting",
                from_statuses=("parsed", "extracting"),
                max_claims=2,
            )
            == "claimed"
        )
        assert await job_queries.record_done(connection, state.job_id, extract_ref)
        assert (
            await job_queries.mark_failed(
                connection,
                state.job_id,
                "timeout",
                from_statuses=("queued", "parsing", "extracting"),
            )
            == "skipped"
        )

    stored = await _get(state.job_id)
    assert stored is not None
    assert stored.status == "done"
    assert stored.extract_result_ref == extract_ref
    assert stored.parsing_claims == 1
    assert stored.extracting_claims == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_mark_failed_skips_a_missing_job(database: str) -> None:
    async with db_engine.transaction() as connection:
        missing = uuid.uuid4()
        assert (
            await job_queries.mark_failed(
                connection, missing, "internal_error", from_statuses=("queued",)
            )
            == "skipped"
        )
        assert (
            await job_queries.claim(
                connection,
                missing,
                status="parsing",
                from_statuses=("queued", "parsing"),
                max_claims=1,
            )
            == "missing"
        )


@pytest.mark.integration
@pytest.mark.anyio
async def test_a_write_does_not_require_a_valid_job_state(database: str) -> None:
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

    async with db_engine.transaction() as connection:
        assert await job_queries.record_parsed(connection, job_id, str(uuid.uuid4()))
    with pytest.raises(CorruptJobStateError):
        await _get(job_id)


@pytest.mark.integration
@pytest.mark.anyio
async def test_concurrent_claims_each_count(database: str) -> None:
    import asyncio

    state = _queued(uuid.uuid4())
    await _create(state)

    async def once() -> str:
        async with db_engine.transaction() as connection:
            return await job_queries.claim(
                connection,
                state.job_id,
                status="parsing",
                from_statuses=("queued", "parsing"),
                max_claims=2,
            )

    assert sorted(await asyncio.gather(once(), once())) == ["claimed", "claimed"]
    stored = await _get(state.job_id)
    assert stored is not None
    assert stored.parsing_claims == 2


@pytest.mark.integration
@pytest.mark.anyio
async def test_concurrent_jobs_remain_independent(database: str) -> None:
    import asyncio

    job_ids = [uuid.uuid4() for _ in range(10)]
    for job_id in job_ids:
        await _create(_queued(job_id))

    async def claim_one(job_id: uuid.UUID) -> None:
        async with db_engine.transaction() as connection:
            assert (
                await job_queries.claim(
                    connection,
                    job_id,
                    status="parsing",
                    from_statuses=("queued", "parsing"),
                    max_claims=2,
                )
                == "claimed"
            )

    await asyncio.gather(*(claim_one(job_id) for job_id in job_ids))

    for job_id in job_ids:
        state = await _get(job_id)
        assert state is not None
        assert state.status == "parsing"
        assert state.parsing_claims == 1


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
    async with db_engine.transaction() as connection:
        await job_queries.claim(
            connection,
            state.job_id,
            status="parsing",
            from_statuses=("queued", "parsing"),
            max_claims=2,
        )
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
