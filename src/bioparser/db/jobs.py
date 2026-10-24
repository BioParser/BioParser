"""Job rows on the shared Postgres connection."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import cast, get_args
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import CheckConstraint, DateTime, String, Text, Uuid, insert, select, text
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.orm import Mapped, mapped_column

from bioparser.artifact_paths import ARTIFACT_PATH_MAX_LENGTH
from bioparser.db.base import Base
from bioparser.jobstate.errors import (
    CorruptJobStateError,
    JobNotFoundError,
    StaleJobStateWriteError,
)
from bioparser.jobstate.models import ALLOWED_TRANSITIONS, JobState, JobStatus

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


async def _locked_status(connection: AsyncConnection, job_id: UUID) -> str:
    row = (
        await connection.execute(select(Job.status).where(Job.job_id == job_id).with_for_update())
    ).one_or_none()
    if row is None:
        raise JobNotFoundError(str(job_id))
    return str(row.status)


async def update(connection: AsyncConnection, state: JobState) -> None:
    """Replace one job when the new status is allowed from the stored one.

    Locks the row, checks ALLOWED_TRANSITIONS, then writes, on the caller's
    transaction. A stored status this process does not know is overwritten,
    matching the old Redis script. The statement does not set updated_at; the
    row trigger does.
    """
    stored = await _locked_status(connection, state.job_id)
    if stored in ALLOWED_TRANSITIONS:
        allowed = ALLOWED_TRANSITIONS[cast(JobStatus, stored)]
        if state.status not in allowed:
            raise StaleJobStateWriteError(state.status)
    await connection.execute(
        sql_update(Job).where(Job.job_id == state.job_id).values(**_job_state_to_columns(state))
    )
