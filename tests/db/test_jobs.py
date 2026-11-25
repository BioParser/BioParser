"""Job query coverage. The Postgres tests need BIOPARSER_TEST_DATABASE_URL."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import get_args

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import create_async_engine

from bioparser.db import documents as document_queries
from bioparser.db import engine as db_engine
from bioparser.db import jobs as job_queries
from bioparser.db.engine import (
    DatabaseError,
    DatabaseUnavailableError,
    async_database_url,
    sync_database_url,
)
from bioparser.db.migrate import upgrade
from bioparser.jobs import CorruptJobStateError, JobState, JobStatus

CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
CHECKSUM = "ab" * 32


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
        "document_checksum": CHECKSUM,
        "created_at": CREATED_AT,
    }
    fields.update(overrides)
    return JobState.model_validate(fields)


async def _create(state: JobState) -> JobState:
    async with db_engine.transaction() as connection:
        await document_queries.create(
            connection,
            checksum=state.document_checksum,
            display_name=None,
            size_bytes=0,
        )
        return await job_queries.create(connection, state)


async def _get(job_id: uuid.UUID) -> JobState | None:
    async with db_engine.transaction() as connection:
        return await job_queries.get(connection, job_id)


async def _deleted_at(job_id: uuid.UUID) -> datetime | None:
    """The stored deleted_at. The row must exist."""
    async with db_engine.transaction() as connection:
        deleted_at: datetime | None = (
            await connection.execute(
                text("SELECT deleted_at FROM jobs WHERE job_id = :job_id"), {"job_id": job_id}
            )
        ).scalar_one()
        return deleted_at


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
    created = await _create(state)

    assert created.created_at != state.created_at
    assert await _get(state.job_id) == created


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
async def test_create_rejects_a_checksum_with_no_document(database: str) -> None:
    state = _queued(uuid.uuid4(), document_checksum="11" * 32)
    with pytest.raises(DatabaseError, match="Postgres query failed"):
        async with db_engine.transaction() as connection:
            await job_queries.create(connection, state)


@pytest.mark.integration
@pytest.mark.anyio
async def test_job_reads_join_the_document_including_a_missing_name(database: str) -> None:
    named_checksum = "ab" * 32
    unnamed_checksum = "cd" * 32
    named = _queued(uuid.uuid4(), document_checksum=named_checksum)
    unnamed = _queued(uuid.uuid4(), document_checksum=unnamed_checksum)
    async with db_engine.transaction() as connection:
        await document_queries.create(
            connection,
            checksum=named_checksum,
            display_name="paper.pdf",
            size_bytes=4,
        )
        await document_queries.create(
            connection,
            checksum=unnamed_checksum,
            display_name=None,
            size_bytes=0,
        )
        await job_queries.create(connection, named)
        await job_queries.create(connection, unnamed)

    async with db_engine.transaction() as connection:
        loaded_rows = await job_queries.list_jobs(connection, job_id=named.job_id)
        missing_rows = await job_queries.list_jobs(connection, job_id=unnamed.job_id)
        listed = await job_queries.list_jobs(connection)
        named_jobs = await job_queries.list_jobs_for_document(connection, named_checksum)
        absent = await job_queries.list_jobs_for_document(connection, "ef" * 32)

    assert len(loaded_rows) == 1
    loaded_state, loaded_name, loaded_title = loaded_rows[0]
    assert loaded_state.document_checksum == named_checksum
    assert loaded_name == "paper.pdf"
    assert loaded_title is None
    assert len(missing_rows) == 1
    assert missing_rows[0][1] is None
    names = {state.job_id: name for state, name, _title in listed}
    assert names[named.job_id] == "paper.pdf"
    assert names[unnamed.job_id] is None
    assert named_jobs is not None
    assert [state.job_id for state, _name, _title in named_jobs] == [named.job_id]
    assert absent is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_a_document_with_no_jobs_lists_nothing(database: str) -> None:
    checksum = "ef" * 32
    async with db_engine.transaction() as connection:
        await document_queries.create(
            connection,
            checksum=checksum,
            display_name="empty.pdf",
            size_bytes=1,
        )
        jobs = await job_queries.list_jobs_for_document(connection, checksum)
    assert jobs == []


@pytest.mark.integration
@pytest.mark.anyio
async def test_claim_counts_a_start_and_record_parsed_keeps_the_count(database: str) -> None:
    state = _queued(uuid.uuid4())
    await _create(state)
    artifact = str(uuid.uuid4())

    async with db_engine.transaction() as connection:
        assert not await job_queries.record_parsed(connection, state.job_id, artifact)
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
        assert not await job_queries.record_done(connection, state.job_id, extract_ref)
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
            == "missing"
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


# What each status needs on a stored job besides its id: refs and error
_STATUS_FIELDS: dict[str, dict[str, object]] = {
    "queued": {},
    "parsing": {},
    "parsed": {"parse_result_ref": "parsed"},
    "extracting": {"parse_result_ref": "parsed"},
    "done": {"parse_result_ref": "parsed", "extract_result_ref": "extracted"},
    "failed": {"error_code": "invalid_pdf"},
}


@pytest.mark.integration
@pytest.mark.anyio
async def test_delete_finished_removes_only_a_done_or_failed_job(database: str) -> None:
    states = {
        status: _queued(uuid.uuid4(), status=status, **fields)
        for status, fields in _STATUS_FIELDS.items()
    }
    for state in states.values():
        await _create(state)

    async with db_engine.transaction() as connection:
        outcomes = {
            status: await job_queries.delete_finished(connection, state.job_id)
            for status, state in states.items()
        }
        missing = await job_queries.delete_finished(connection, uuid.uuid4())
        document = await document_queries.get(connection, CHECKSUM)
        remaining = await job_queries.list_jobs_for_document(connection, CHECKSUM)

    assert outcomes == {
        "queued": "not_finished",
        "parsing": "not_finished",
        "parsed": "not_finished",
        "extracting": "not_finished",
        "done": "deleted",
        "failed": "deleted",
    }
    assert missing == "missing"
    assert document is not None
    assert remaining is not None
    assert {state.status for state, _name, _title in remaining} == {
        "queued",
        "parsing",
        "parsed",
        "extracting",
    }
    # A soft delete: the rows stay, marked deleted, and the active jobs are not marked.
    for status, state in states.items():
        deleted_at = await _deleted_at(state.job_id)
        assert (deleted_at is not None) == (status in ("done", "failed")), status


@pytest.mark.integration
@pytest.mark.anyio
async def test_delete_finished_removes_only_the_given_job(database: str) -> None:
    """Other finished jobs of the same document and of other documents stay."""
    other_checksum = "cd" * 32
    deleted = _queued(uuid.uuid4(), status="done", **_STATUS_FIELDS["done"])
    same_document = _queued(uuid.uuid4(), status="done", **_STATUS_FIELDS["done"])
    other_document = _queued(
        uuid.uuid4(), status="failed", document_checksum=other_checksum, **_STATUS_FIELDS["failed"]
    )
    for state in (deleted, same_document, other_document):
        await _create(state)

    async with db_engine.transaction() as connection:
        assert await job_queries.delete_finished(connection, deleted.job_id) == "deleted"
        listed = await job_queries.list_jobs(connection)

    assert {state.job_id for state, _name, _title in listed} == {
        same_document.job_id,
        other_document.job_id,
    }


@pytest.mark.integration
@pytest.mark.anyio
async def test_delete_finished_checks_the_status_it_deletes(database: str) -> None:
    """A job that moves on after a read is not deleted by a stale decision."""
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
        assert await job_queries.delete_finished(connection, state.job_id) == "not_finished"
        await job_queries.mark_failed(
            connection, state.job_id, "parse_failed", from_statuses=("parsing",)
        )
        assert await job_queries.delete_finished(connection, state.job_id) == "deleted"
    assert await _deleted_at(state.job_id) is not None


@pytest.mark.integration
@pytest.mark.anyio
async def test_a_deleted_job_leaves_the_lists_and_keeps_its_row(database: str) -> None:
    state = _queued(uuid.uuid4(), status="done", **_STATUS_FIELDS["done"])
    await _create(state)

    async with db_engine.transaction() as connection:
        assert await job_queries.delete_finished(connection, state.job_id) == "deleted"
        listed = await job_queries.list_jobs(connection)
        by_id = await job_queries.list_jobs(connection, job_id=state.job_id)
        for_document = await job_queries.list_jobs_for_document(connection, CHECKSUM)
        stored = await job_queries.get(connection, state.job_id)

    assert listed == []
    assert by_id == []
    # The document is still there, with no jobs to list.
    assert for_document == []
    # The workers still read it, and skip it by its status.
    assert stored is not None
    assert stored.status == "done"


@pytest.mark.integration
@pytest.mark.anyio
async def test_deleting_a_deleted_job_is_missing_and_keeps_the_first_time(database: str) -> None:
    state = _queued(uuid.uuid4(), status="failed", **_STATUS_FIELDS["failed"])
    await _create(state)
    async with db_engine.transaction() as connection:
        assert await job_queries.delete_finished(connection, state.job_id) == "deleted"
    first = await _deleted_at(state.job_id)

    async with db_engine.transaction() as connection:
        assert await job_queries.delete_finished(connection, state.job_id) == "missing"

    assert await _deleted_at(state.job_id) == first


@pytest.mark.integration
@pytest.mark.anyio
async def test_the_database_refuses_to_delete_an_active_job(database: str) -> None:
    """jobs_deleted_at_check holds the rule even for a write that skips delete_finished."""
    state = _queued(uuid.uuid4())
    await _create(state)

    with pytest.raises(DatabaseError):
        async with db_engine.transaction() as connection:
            await connection.execute(
                text("UPDATE jobs SET deleted_at = now() WHERE job_id = :job_id"),
                {"job_id": state.job_id},
            )

    assert await _deleted_at(state.job_id) is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_a_write_does_not_require_a_valid_job_state(database: str) -> None:
    job_id = uuid.uuid4()
    engine = create_engine(sync_database_url(database))
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO documents (checksum, display_name, size_bytes) "
                "VALUES (:checksum, NULL, 0)"
            ),
            {"checksum": CHECKSUM},
        )
        connection.execute(
            text(
                "INSERT INTO jobs (job_id, schema_version, status, parser, extractor, "
                "document_checksum) "
                "VALUES (:job_id, 1, 'parsing', 'default', 'default', :checksum)"
            ),
            {"job_id": job_id, "checksum": CHECKSUM},
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
                "INSERT INTO documents (checksum, display_name, size_bytes) "
                "VALUES (:checksum, NULL, 0)"
            ),
            {"checksum": CHECKSUM},
        )
        connection.execute(
            text(
                "INSERT INTO jobs (job_id, schema_version, status, parser, extractor, "
                "document_checksum) "
                "VALUES (:job_id, 1, 'queued', 'default', 'default', :checksum)"
            ),
            {"job_id": job_id, "checksum": CHECKSUM},
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
        with pytest.raises(DatabaseUnavailableError) as raised:
            await _create(_queued(uuid.uuid4()))
        assert "s3cret" not in str(raised.value)
    finally:
        await db_engine.aclose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_pool_exhaustion_raises_database_error(database: str) -> None:
    small = create_async_engine(
        db_engine.async_database_url(database), pool_size=1, max_overflow=0, pool_timeout=0.2
    )
    # Swap the pool directly. monkeypatch would put the real pool back after this
    # fixture has already closed whichever pool it found, and the next test would
    # see that pool still open.
    original = db_engine._engine
    db_engine._engine = small
    try:
        async with db_engine.transaction():
            with pytest.raises(DatabaseUnavailableError, match="TimeoutError"):
                await _get(uuid.uuid4())
    finally:
        db_engine._engine = original
        await small.dispose()


def _engine_that_records(events: list[str]) -> object:
    class _Engine:
        @asynccontextmanager
        async def begin(self) -> AsyncIterator[object]:
            try:
                yield object()
                events.append("commit")
            except BaseException:
                events.append("rollback")
                raise
            finally:
                events.append("close")

    return _Engine()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected_type"),
    [
        (OSError, DatabaseUnavailableError),
        (TimeoutError, DatabaseUnavailableError),
    ],
)
async def test_transaction_reports_a_driver_error_from_the_body(
    monkeypatch: pytest.MonkeyPatch,
    error: type[Exception],
    expected_type: type[DatabaseError],
) -> None:
    events: list[str] = []
    monkeypatch.setattr(db_engine, "_engine", _engine_that_records(events))
    with pytest.raises(DatabaseError, match=error.__name__) as raised:
        async with db_engine.transaction():
            raise error()
    assert type(raised.value) is expected_type
    assert events == ["rollback", "close"]


def test_database_error_from_driver_treats_invalidated_connection_as_outage() -> None:
    driver_error = OperationalError("connection failed", {}, Exception("disconnect"))
    driver_error.connection_invalidated = True
    raised = db_engine._database_error_from_driver(driver_error)
    assert type(raised) is DatabaseUnavailableError


class _PgError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    ("driver_error", "expected_type"),
    [
        (PoolTimeoutError("pool", None, None), DatabaseUnavailableError),
        (InterfaceError("interface", None, Exception("broken")), DatabaseUnavailableError),
        (ConnectionRefusedError(), DatabaseUnavailableError),
        (TimeoutError(), DatabaseUnavailableError),
        (OperationalError("statement", {}, Exception("deadlock")), DatabaseError),
        (IntegrityError("statement", {}, _PgError("23505")), DatabaseError),
        (DBAPIError("statement", {}, _PgError("57014")), DatabaseError),
        (DBAPIError("statement", {}, _PgError("40001")), DatabaseError),
        (DBAPIError("connect", {}, _PgError("08001")), DatabaseUnavailableError),
    ],
    ids=[
        "pool_timeout",
        "interface",
        "connect_oserror",
        "timeout",
        "operational_statement",
        "integrity",
        "query_canceled_sqlstate",
        "serialization_failure",
        "connection_sqlstate",
    ],
)
def test_database_error_from_driver_classifies_connection_failures(
    driver_error: BaseException,
    expected_type: type[DatabaseError],
) -> None:
    raised = db_engine._database_error_from_driver(driver_error)
    assert type(raised) is expected_type


@pytest.mark.parametrize("sqlstate", ["57P03", "57P01", "08006", "53300"])
def test_a_server_that_does_not_serve_now_is_an_outage(sqlstate: str) -> None:
    error = DBAPIError("connect", {}, _PgError(sqlstate))
    assert type(db_engine._database_error_from_driver(error)) is DatabaseUnavailableError


@pytest.mark.anyio
async def test_transaction_passes_through_an_unrelated_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(db_engine, "_engine", _engine_that_records(events))
    with pytest.raises(ValueError, match="not the database"):
        async with db_engine.transaction():
            raise ValueError("not the database")
    assert events == ["rollback", "close"]


def test_job_columns_match_the_job_state_fields() -> None:
    columns = {column.name for column in job_queries.Job.__table__.columns}
    assert columns - {"updated_at", "deleted_at"} == set(JobState.model_fields)


@pytest.mark.integration
@pytest.mark.anyio
async def test_migrated_status_check_accepts_exactly_the_job_statuses(database: str) -> None:
    """The migration lists the statuses by hand. This fails when the code adds one."""
    insert = text(
        "INSERT INTO documents (checksum, display_name, size_bytes) "
        "VALUES (:checksum, NULL, 0) ON CONFLICT (checksum) DO NOTHING"
    )
    job_insert = text(
        "INSERT INTO jobs (job_id, schema_version, status, parser, extractor, document_checksum) "
        "VALUES (:job_id, 2, :status, 'default', 'default', :checksum)"
    )
    engine = create_engine(sync_database_url(database))
    try:
        with engine.begin() as connection:
            connection.execute(insert, {"checksum": CHECKSUM})
        for status in get_args(JobStatus):
            with engine.begin() as connection:
                connection.execute(
                    job_insert,
                    {"job_id": uuid.uuid4(), "status": status, "checksum": CHECKSUM},
                )
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                job_insert,
                {"job_id": uuid.uuid4(), "status": "not_a_status", "checksum": CHECKSUM},
            )
    finally:
        engine.dispose()
