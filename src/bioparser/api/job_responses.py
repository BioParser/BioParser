"""Build the public job bodies. Routes stay in ``app``."""

import asyncio
import logging
from typing import Any, NoReturn, cast

from fastapi import Request
from pydantic import ValidationError

from bioparser.ets.adapter import to_ets
from bioparser.extract.models import ExtractionArtifact
from bioparser.jobs import JobState, SafeError
from bioparser.jobs.state import FailedStage
from bioparser.logging_config import error_fields
from bioparser.storage import (
    ArtifactNotFoundError,
    ArtifactStorage,
    StorageError,
    StoragePayloadError,
)

from .errors import (
    ARTIFACT_INVALID,
    STORAGE_UNAVAILABLE,
    http_error,
)
from .schema import (
    EtsRowsResponse,
    FailedJobFullResponse,
    FailedJobResponse,
    JobFullResponse,
    JobResponse,
    JobStatusResponse,
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


def _record(state: JobState, display_name: str | None, title: str | None) -> dict[str, Any]:
    """The stored record. No storage paths."""
    return {
        "job_id": state.job_id,
        "created_at": state.created_at,
        "display_name": display_name,
        "title": title,
        "parser": state.parser,
        "extractor": state.extractor,
        "document_checksum": state.document_checksum,
    }


def _error(state: JobState) -> SafeError:
    if state.error_code is None:
        raise ValueError("Failed job is missing error_code")
    return SafeError(code=state.error_code)


def _failed_stage(state: JobState) -> FailedStage:
    stage = state.failed_stage
    if stage is None:
        raise ValueError("Failed job is missing failed_stage")
    return stage


def job_body(state: JobState) -> JobStatusResponse:
    """The public status. No record fields, and no ETS rows."""
    if state.status == "failed":
        return FailedJobResponse(
            job_id=state.job_id,
            status="failed",
            error=_error(state),
            failed_stage=_failed_stage(state),
        )
    return JobResponse(job_id=state.job_id, status=state.status)


def job_body_full(
    state: JobState, display_name: str | None, title: str | None
) -> JobStatusResponse:
    """The public status with the stored record. Still no ETS rows."""
    if state.status == "failed":
        return FailedJobFullResponse(
            status="failed",
            error=_error(state),
            failed_stage=_failed_stage(state),
            **_record(state, display_name, title),
        )
    return JobFullResponse(status=state.status, **_record(state, display_name, title))


def _ets_rows(payload: bytes, references: str, job_id: object) -> EtsRowsResponse:
    """Map one stored artifact into the public ETS body."""
    try:
        artifact = ExtractionArtifact.model_validate_json(payload)
        result = to_ets(artifact, references=references)
    except ValueError as exc:
        _raise_invalid(exc, job_id)
    return EtsRowsResponse(rows=result.rows)


async def job_ets_rows(request: Request, state: JobState) -> EtsRowsResponse:
    """The ETS rows of a done job. A missing or invalid artifact is a safe 500."""
    checksum = state.document_checksum
    ref = state.extract_result_ref
    if ref is None:
        LOGGER.error("done job has no extraction artifact", extra={"job_id": str(state.job_id)})
        raise http_error(500, ARTIFACT_INVALID)
    log_fields = {"job_id": str(state.job_id), "artifact": "extraction"}
    storage = cast(ArtifactStorage, request.app.state.artifact_storage)
    try:
        payload = await storage.retrieve(ref)
        return await asyncio.to_thread(_ets_rows, payload, checksum, state.job_id)
    except (ArtifactNotFoundError, StoragePayloadError) as exc:
        LOGGER.error("job artifact missing or invalid", extra=log_fields | error_fields(exc))
        raise http_error(500, ARTIFACT_INVALID) from exc
    except _InvalidArtifact as exc:
        raise http_error(500, ARTIFACT_INVALID) from exc
    except StorageError as exc:
        LOGGER.warning("job artifact read failed", extra=log_fields | error_fields(exc))
        raise http_error(503, STORAGE_UNAVAILABLE) from exc
