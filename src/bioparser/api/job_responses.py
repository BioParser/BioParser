"""Build the public job bodies. Routes stay in ``app``."""

import asyncio
import logging
from typing import Any, Literal, NoReturn, cast

from fastapi import Request
from pydantic import ValidationError

from bioparser.artifact_paths import ArtifactPathError, pdf_checksum
from bioparser.ets.adapter import to_ets
from bioparser.ets.models import EtsRow
from bioparser.extract.models import ExtractionArtifact
from bioparser.jobs import JobState, SafeError
from bioparser.logging_config import error_fields
from bioparser.storage import (
    ArtifactNotFoundError,
    ArtifactStorage,
    StorageError,
    StoragePayloadError,
)

from .errors import ARTIFACT_INVALID, JOB_STATE_UNREADABLE, STORAGE_UNAVAILABLE, http_error
from .schema import (
    DoneJobFullResponse,
    DoneJobResponse,
    FailedJobFullResponse,
    FailedJobResponse,
    JobListResponse,
    ListedJobFullResponse,
    ListedJobResponse,
    OpenJobFullResponse,
    OpenJobResponse,
)

LOGGER = logging.getLogger(__name__)


class _InvalidArtifact(Exception):
    """The stored extraction artifact does not validate."""


def _raise_invalid(exc: ValueError, job_id: object) -> NoReturn:
    """Log a mapping failure without its values, then mark the artifact invalid."""
    extra: dict[str, object] = {"job_id": str(job_id), "artifact": "extraction"}
    extra.update(error_fields(exc))
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_input=False)
        extra["invalid_field_count"] = len(errors)
        extra["invalid_fields"] = [
            ".".join(str(part) for part in error["loc"]) for error in errors[:20]
        ]
    LOGGER.error("job artifact missing or invalid", extra=extra)
    raise _InvalidArtifact from exc


def job_pdf_checksum(state: JobState) -> str:
    """Return a job's document checksum or the public corrupt-state error."""
    try:
        return pdf_checksum(state.pdf_ref)
    except ArtifactPathError as exc:
        LOGGER.error("job PDF reference unreadable", extra={"job_id": str(state.job_id)})
        raise http_error(500, JOB_STATE_UNREADABLE) from exc


def _record(state: JobState, display_name: str | None) -> dict[str, Any]:
    """The stored record. No storage paths."""
    return {
        "job_id": state.job_id,
        "created_at": state.created_at,
        "display_name": display_name,
        "parser": state.parser,
        "extractor": state.extractor,
        "document_checksum": job_pdf_checksum(state),
        "has_parse_result": state.parse_result_ref is not None,
    }


def failed_job(state: JobState) -> FailedJobResponse:
    if state.error_code is None:
        raise ValueError("Failed job is missing error_code")
    return FailedJobResponse(
        job_id=state.job_id,
        status="failed",
        error=SafeError(code=state.error_code),
    )


def failed_job_full(state: JobState, display_name: str | None) -> FailedJobFullResponse:
    if state.error_code is None:
        raise ValueError("Failed job is missing error_code")
    return FailedJobFullResponse(
        status="failed",
        error=SafeError(code=state.error_code),
        **_record(state, display_name),
    )


def listed_job(state: JobState) -> JobListResponse:
    """The public status for a list. No record fields, and no ETS rows."""
    if state.status == "failed":
        return failed_job(state)
    return ListedJobResponse(job_id=state.job_id, status=state.status)


def listed_job_full(state: JobState, display_name: str | None) -> JobListResponse:
    """A list row with the stored record. Still no ETS rows."""
    if state.status == "failed":
        return failed_job_full(state, display_name)
    return ListedJobFullResponse(status=state.status, **_record(state, display_name))


def open_job(state: JobState) -> OpenJobResponse:
    if state.status not in ("queued", "parsing", "parsed", "extracting"):
        raise ValueError(f"Job {state.job_id} is {state.status}, not open")
    return OpenJobResponse(job_id=state.job_id, status=state.status)


def open_job_full(state: JobState, display_name: str | None) -> OpenJobFullResponse:
    if state.status not in ("queued", "parsing", "parsed", "extracting"):
        raise ValueError(f"Job {state.job_id} is {state.status}, not open")
    return OpenJobFullResponse(status=state.status, **_record(state, display_name))


def _done_body(
    payload: bytes,
    references: str,
    detail: Literal["ets", "full"],
    fields: dict[str, Any],
) -> DoneJobResponse | DoneJobFullResponse:
    """Map one stored artifact into the public done-job body."""
    try:
        artifact = ExtractionArtifact.model_validate_json(payload)
        result = to_ets(artifact, references=references)
    except ValueError as exc:
        _raise_invalid(exc, fields["job_id"])
    if detail == "full":
        return DoneJobFullResponse(
            status="done",
            rows=result.rows,
            **fields,
        )
    try:
        rows = [
            EtsRow.model_validate(row.model_dump(exclude={"source_index"})) for row in result.rows
        ]
    except ValidationError as exc:
        _raise_invalid(exc, fields["job_id"])
    return DoneJobResponse(
        status="done",
        rows=rows,
        **fields,
    )


async def done_job(
    request: Request,
    state: JobState,
    *,
    detail: Literal["ets", "full"],
    display_name: str | None,
) -> DoneJobResponse | DoneJobFullResponse:
    """The ETS body of a done job. A missing or invalid artifact is a safe 500.

    ``detail="full"`` adds the stored record and each row's observation index.
    """
    checksum = job_pdf_checksum(state)
    fields = _record(state, display_name) if detail == "full" else {"job_id": state.job_id}
    ref = state.extract_result_ref
    if ref is None:
        LOGGER.error("done job has no extraction artifact", extra={"job_id": str(state.job_id)})
        raise http_error(500, ARTIFACT_INVALID)
    log_fields = {"job_id": str(state.job_id), "artifact": "extraction"}
    storage = cast(ArtifactStorage, request.app.state.artifact_storage)
    try:
        payload = await storage.retrieve(ref)
        return await asyncio.to_thread(_done_body, payload, checksum, detail, fields)
    except (ArtifactNotFoundError, StoragePayloadError) as exc:
        LOGGER.error("job artifact missing or invalid", extra=log_fields | error_fields(exc))
        raise http_error(500, ARTIFACT_INVALID) from exc
    except _InvalidArtifact as exc:
        raise http_error(500, ARTIFACT_INVALID) from exc
    except StorageError as exc:
        LOGGER.warning("job artifact read failed", extra=log_fields | error_fields(exc))
        raise http_error(503, STORAGE_UNAVAILABLE) from exc
