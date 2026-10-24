"""Job rows on the shared Postgres connection."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Literal, cast, get_args
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import CheckConstraint, DateTime, String, Text, Uuid, insert, select, text
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.orm import InstrumentedAttribute, Mapped, mapped_column

from bioparser.artifact_paths import ARTIFACT_PATH_MAX_LENGTH
from bioparser.db.base import Base
from bioparser.jobstate.errors import CorruptJobStateError
from bioparser.jobstate.models import ALLOWED_TRANSITIONS, JobState, JobStatus, SafeErrorCode

# The same status list the column check uses. Schema version, parser names,
# extractor names, and error codes stay unconstrained apart from null.
_STATUSES = get_args(JobStatus)


class Job(Base):
    """One row of the jobs table. Queries and Alembic both use this class.

    The database fills created_at and updated_at. The update trigger that
    refreshes updated_at lives in the migration; a column cannot express it.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(status) for status in _STATUSES)})",
            name="jobs_status_check",
        ),
        CheckConstraint("pdf_ref <> ''", name="jobs_pdf_ref_check"),
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
    )

    job_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    schema_version: Mapped[int]
    status: Mapped[str] = mapped_column(Text)
    parser: Mapped[str] = mapped_column(Text)
    extractor: Mapped[str] = mapped_column(Text)
    pdf_ref: Mapped[str] = mapped_column(String(ARTIFACT_PATH_MAX_LENGTH))
    parse_result_ref: Mapped[str | None] = mapped_column(String(ARTIFACT_PATH_MAX_LENGTH))
    extract_result_ref: Mapped[str | None] = mapped_column(String(ARTIFACT_PATH_MAX_LENGTH))
    error_code: Mapped[str | None] = mapped_column(Text)
    parsing_claims: Mapped[int] = mapped_column(server_default=text("0"))
    extracting_claims: Mapped[int] = mapped_column(server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


# Names shared by JobState and the jobs table. created_at and updated_at stay out:
# the database fills them.
_COLUMNS = (
    "job_id",
    "schema_version",
    "status",
    "parser",
    "extractor",
    "pdf_ref",
    "parse_result_ref",
    "extract_result_ref",
    "error_code",
    "parsing_claims",
    "extracting_claims",
)


def _job_state_to_columns(state: JobState) -> dict[str, object]:
    """Column values for one job. The database sets created_at and updated_at."""
    return {name: getattr(state, name) for name in _COLUMNS}


def _row_to_job_state(row: Mapping[str, object]) -> JobState:
    return JobState.model_validate({name: row[name] for name in _COLUMNS})


async def create(connection: AsyncConnection, state: JobState) -> None:
    """Insert one job."""
    await connection.execute(insert(Job).values(**_job_state_to_columns(state)))


async def get(connection: AsyncConnection, job_id: UUID) -> JobState | None:
    """Load one job, or None when the row is missing. An invalid row is corrupt."""
    row = (
        (await connection.execute(select(Job.__table__).where(Job.job_id == job_id)))
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    try:
        return _row_to_job_state(cast(Mapping[str, object], row))
    except ValidationError as exc:
        raise CorruptJobStateError(str(job_id)) from exc


ClaimOutcome = Literal["claimed", "exhausted", "skipped", "missing"]
MarkOutcome = Literal["recorded", "not_allowed", "skipped"]


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
            .values(status="failed", error_code="internal_error")
        )
        if exhausted.rowcount == 1:
            return "exhausted"
    found = (await connection.execute(select(Job.job_id).where(Job.job_id == job_id))).one_or_none()
    if found is None:
        return "missing"
    return "skipped"


async def record_parsed(connection: AsyncConnection, job_id: UUID, artifact_path: str) -> bool:
    """Set `parsed` and the parser artifact while the job is still `queued` or `parsing`."""
    result = await connection.execute(
        sql_update(Job)
        .where(Job.job_id == job_id, Job.status.in_(("queued", "parsing")))
        .values(status="parsed", parse_result_ref=artifact_path)
    )
    return result.rowcount == 1


async def record_done(connection: AsyncConnection, job_id: UUID, artifact_path: str) -> bool:
    """Set `done` and the extraction artifact while the job is still `parsed` or `extracting`."""
    result = await connection.execute(
        sql_update(Job)
        .where(Job.job_id == job_id, Job.status.in_(("parsed", "extracting")))
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
    is missing or has another status.
    """
    failable = _failable(from_statuses)
    if failable:
        result = await connection.execute(
            sql_update(Job)
            .where(Job.job_id == job_id, Job.status.in_(failable))
            .values(status="failed", error_code=code)
        )
        if result.rowcount == 1:
            return "recorded"
    row = (await connection.execute(select(Job.status).where(Job.job_id == job_id))).one_or_none()
    if row is None:
        return "skipped"
    status = str(row.status)
    if status in from_statuses and (
        status not in ALLOWED_TRANSITIONS
        or "failed" not in ALLOWED_TRANSITIONS[cast(JobStatus, status)]
    ):
        return "not_allowed"
    return "skipped"


def _failable(from_statuses: Collection[str]) -> list[str]:
    """Statuses in `from_statuses` that may become `failed`."""
    return [
        status
        for status in from_statuses
        if status in ALLOWED_TRANSITIONS
        and "failed" in ALLOWED_TRANSITIONS[cast(JobStatus, status)]
    ]


def _claim_count(status: JobStatus) -> InstrumentedAttribute[int]:
    if status == "parsing":
        return Job.parsing_claims
    if status == "extracting":
        return Job.extracting_claims
    raise ValueError(f"status={status!r} cannot be claimed")
