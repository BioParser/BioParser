"""Job rows on the shared Postgres connection."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Literal, cast, get_args
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    and_,
    insert,
    select,
    text,
)
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.orm import InstrumentedAttribute, Mapped, mapped_column

from bioparser.artifact_paths import ARTIFACT_PATH_MAX_LENGTH
from bioparser.db.base import Base
from bioparser.db.documents import Document
from bioparser.hashing import Sha256Hex
from bioparser.jobs.state import (
    ALLOWED_TRANSITIONS,
    CorruptJobStateError,
    JobState,
    JobStatus,
    SafeErrorCode,
)

# The same status list the column check uses. Schema version, parser names,
# extractor names, and error codes stay unconstrained apart from null.
_STATUSES = get_args(JobStatus)


class Job(Base):
    """One row of the jobs table. Queries and Alembic both use this class.

    The database fills created_at and updated_at. The update trigger that
    refreshes updated_at lives in the migration; a column cannot express it.
    deleted_at is set when a finished job is deleted: the row stays, and the API
    reads leave it out.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(status) for status in _STATUSES)})",
            name="jobs_status_check",
        ),
        Index("jobs_document_checksum_idx", "document_checksum"),
        CheckConstraint(
            "parse_result_ref IS NULL OR parse_result_ref <> ''",
            name="jobs_parse_result_ref_check",
        ),
        CheckConstraint(
            "extract_result_ref IS NULL OR extract_result_ref <> ''",
            name="jobs_extract_result_ref_check",
        ),
        CheckConstraint("parsing_claims >= 0", name="jobs_parsing_claims_check"),
        CheckConstraint("extracting_claims >= 0", name="jobs_extracting_claims_check"),
        CheckConstraint(
            "deleted_at IS NULL OR status IN ('done', 'failed')",
            name="jobs_deleted_at_check",
        ),
        CheckConstraint(
            "("
            "status <> 'failed' AND failed_stage IS NULL"
            ") OR ("
            "status = 'failed' AND ("
            "(failed_stage IN ('queued', 'parsing') AND parse_result_ref IS NULL)"
            " OR (failed_stage IN ('parsed', 'extracting') AND parse_result_ref IS NOT NULL)"
            ")"
            ")",
            name="jobs_failed_stage_check",
        ),
    )

    job_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    schema_version: Mapped[int]
    status: Mapped[str] = mapped_column(Text)
    parser: Mapped[str] = mapped_column(Text)
    extractor: Mapped[str] = mapped_column(Text)
    document_checksum: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("documents.checksum", name="jobs_document_checksum_fkey"),
    )
    parse_result_ref: Mapped[str | None] = mapped_column(String(ARTIFACT_PATH_MAX_LENGTH))
    extract_result_ref: Mapped[str | None] = mapped_column(String(ARTIFACT_PATH_MAX_LENGTH))
    error_code: Mapped[str | None] = mapped_column(Text)
    failed_stage: Mapped[str | None] = mapped_column(Text)
    parsing_claims: Mapped[int] = mapped_column(server_default=text("0"))
    extracting_claims: Mapped[int] = mapped_column(server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Every JobState field is a column of the jobs table. created_at and updated_at
# are columns only: the database fills them. deleted_at is a column only too. A
# test checks the two stay in step.
_COLUMNS = tuple(JobState.model_fields)


def _job_state_to_columns(state: JobState) -> dict[str, object]:
    """Column values for one new job. The database sets created_at and updated_at."""
    return {name: getattr(state, name) for name in _COLUMNS if name != "created_at"}


def _row_to_job_state(row: Mapping[str, object]) -> JobState:
    try:
        return JobState.model_validate({name: row[name] for name in _COLUMNS})
    except ValidationError as exc:
        raise CorruptJobStateError(str(row["job_id"])) from exc


def _job_and_name(row: Mapping[str, object]) -> tuple[JobState, str | None, str | None]:
    """The stored job, filename and current title from the joined document row."""
    return (
        _row_to_job_state(row),
        cast(str | None, row["display_name"]),
        cast(str | None, row["title"]),
    )


async def create(connection: AsyncConnection, state: JobState) -> JobState:
    """Insert one job and return it with the database creation time."""
    created_at = (
        await connection.execute(
            insert(Job).values(**_job_state_to_columns(state)).returning(Job.created_at)
        )
    ).scalar_one()
    return state.with_changes(created_at=created_at)


async def get(connection: AsyncConnection, job_id: UUID) -> JobState | None:
    """Load one job, or None when the row is missing. An invalid row is corrupt.

    A deleted job is returned too. The workers read jobs here, and they decide by
    status: a deleted job is `done` or `failed`, so a late delivery for it is
    skipped. The API reads through `list_jobs`, which leaves deleted jobs out.
    """
    row = (
        (await connection.execute(select(Job.__table__).where(Job.job_id == job_id)))
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return _row_to_job_state(cast(Mapping[str, object], row))


async def list_jobs(
    connection: AsyncConnection,
    *,
    job_id: UUID | None = None,
    status: JobStatus | None = None,
    parser: str | None = None,
) -> list[tuple[JobState, str | None, str | None]]:
    """Jobs joined to their document names, newest first. Deleted jobs are left out."""
    stmt = (
        select(Job.__table__, Document.display_name, Document.title)
        .join(Document, Document.checksum == Job.document_checksum)
        .where(Job.deleted_at.is_(None))
    )
    if job_id is not None:
        stmt = stmt.where(Job.job_id == job_id)
    if status is not None:
        stmt = stmt.where(Job.status == status)
    if parser is not None:
        stmt = stmt.where(Job.parser == parser)
    stmt = stmt.order_by(Job.created_at.desc())
    rows = (await connection.execute(stmt)).mappings().all()
    return [_job_and_name(cast(Mapping[str, object], row)) for row in rows]


async def list_jobs_for_document(
    connection: AsyncConnection,
    checksum: Sha256Hex,
) -> list[tuple[JobState, str | None, str | None]] | None:
    """Jobs for one document, newest first. None when that document does not exist.

    Deleted jobs are left out. A document whose jobs are all deleted has an empty list.
    """
    stmt = (
        select(Document.display_name, Document.title, Job.__table__)
        .select_from(Document)
        .outerjoin(Job, and_(Job.document_checksum == Document.checksum, Job.deleted_at.is_(None)))
        .where(Document.checksum == checksum)
        .order_by(Job.created_at.desc())
    )
    rows = (await connection.execute(stmt)).mappings().all()
    if not rows:
        return None
    return [
        _job_and_name(cast(Mapping[str, object], row)) for row in rows if row["job_id"] is not None
    ]


# Only a finished job can be deleted. A queued or running job still has a queue
# message and a worker that moves it on. The jobs_deleted_at_check constraint
# holds the same rule in the database.
DELETABLE_STATUSES: tuple[JobStatus, ...] = ("done", "failed")

DeleteOutcome = Literal["deleted", "not_finished", "missing"]


async def delete_finished(connection: AsyncConnection, job_id: UUID) -> DeleteOutcome:
    """Soft-delete one job while its status is in `DELETABLE_STATUSES`.

    Sets deleted_at; the row stays. The status check and the write are one
    statement, so a job that moves on in between is not deleted. The document, the
    PDF and the artifacts stay too: other jobs of the same PDF can share them.
    `not_finished` means the row is there, not deleted, with another status.
    `missing` means no row, or a row that is already deleted.
    """
    result = await connection.execute(
        sql_update(Job)
        .where(
            Job.job_id == job_id,
            Job.status.in_(DELETABLE_STATUSES),
            Job.deleted_at.is_(None),
        )
        .values(deleted_at=text("now()"))
    )
    if result.rowcount == 1:
        return "deleted"
    row = (
        await connection.execute(select(Job.deleted_at).where(Job.job_id == job_id))
    ).one_or_none()
    if row is None or row.deleted_at is not None:
        return "missing"
    return "not_finished"


ClaimOutcome = Literal["claimed", "exhausted", "skipped", "missing"]
MarkOutcome = Literal["recorded", "not_allowed", "skipped", "missing"]


async def claim(
    connection: AsyncConnection,
    job_id: UUID,
    *,
    status: JobStatus,
    from_statuses: Collection[str],
    max_claims: int,
) -> ClaimOutcome:
    """Start one stage when the row is still in it and under the claim limit.

    The count increases in the same statement as the status change. At the
    limit the row is marked failed when that status may become `failed`, and
    the count stays. `skipped` means the row is still there with another
    status, or the status cannot become `failed`. `missing` means no row.
    """
    count = _claim_count(status)
    claimed = await connection.execute(
        sql_update(Job)
        .where(Job.job_id == job_id, Job.status.in_(from_statuses), count < max_claims)
        .values(status=status, **{count.key: count + 1})
    )
    if claimed.rowcount == 1:
        return "claimed"
    failable = _failable(from_statuses)
    if failable:
        exhausted = await connection.execute(
            sql_update(Job)
            .where(Job.job_id == job_id, Job.status.in_(failable), count >= max_claims)
            .values(status="failed", error_code="retries_exhausted", failed_stage=Job.status)
        )
        if exhausted.rowcount == 1:
            return "exhausted"
    found = (await connection.execute(select(Job.job_id).where(Job.job_id == job_id))).one_or_none()
    if found is None:
        return "missing"
    return "skipped"


async def record_parsed(connection: AsyncConnection, job_id: UUID, artifact_path: str) -> bool:
    """Set `parsed` and the parser artifact while the job is still `parsing`."""
    result = await connection.execute(
        sql_update(Job)
        .where(Job.job_id == job_id, Job.status == "parsing")
        .values(status="parsed", parse_result_ref=artifact_path)
    )
    return result.rowcount == 1


async def record_done(connection: AsyncConnection, job_id: UUID, artifact_path: str) -> bool:
    """Set `done` and the extraction artifact while the job is still `extracting`."""
    result = await connection.execute(
        sql_update(Job)
        .where(Job.job_id == job_id, Job.status == "extracting")
        .values(status="done", extract_result_ref=artifact_path)
    )
    return result.rowcount == 1


async def mark_failed(
    connection: AsyncConnection,
    job_id: UUID,
    code: SafeErrorCode,
    *,
    from_statuses: Collection[str],
) -> MarkOutcome:
    """Set `failed` when one of `from_statuses` may move there.

    `recorded` means the row changed. `not_allowed` means the stored status is
    one of `from_statuses` but cannot become `failed`. `skipped` means the row
    has another status. `missing` means no row.
    """
    failable = _failable(from_statuses)
    if failable:
        result = await connection.execute(
            sql_update(Job)
            .where(Job.job_id == job_id, Job.status.in_(failable))
            .values(status="failed", error_code=code, failed_stage=Job.status)
        )
        if result.rowcount == 1:
            return "recorded"
    row = (await connection.execute(select(Job.status).where(Job.job_id == job_id))).one_or_none()
    if row is None:
        return "missing"
    status = str(row.status)
    if status in from_statuses and not _can_fail(status):
        return "not_allowed"
    return "skipped"


def _can_fail(status: str) -> bool:
    """True when the job may move from `status` to `failed`."""
    return (
        status in ALLOWED_TRANSITIONS and "failed" in ALLOWED_TRANSITIONS[cast(JobStatus, status)]
    )


def _failable(from_statuses: Collection[str]) -> list[str]:
    """Statuses in `from_statuses` that may become `failed`."""
    return [status for status in from_statuses if _can_fail(status)]


def _claim_count(status: JobStatus) -> InstrumentedAttribute[int]:
    if status == "parsing":
        return Job.parsing_claims
    if status == "extracting":
        return Job.extracting_claims
    raise ValueError(f"status={status!r} cannot be claimed")
