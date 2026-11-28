from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from bioparser.ets.models import EtsRow
from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME, ExtractorName
from bioparser.hashing import Sha256Hex
from bioparser.jobs import SafeError
from bioparser.jobs.state import FailedStage, WaitingFor
from bioparser.parser_names import DEFAULT_PARSER_NAME, ParserName


class JobRecord(BaseModel):
    """Fields a `detail=full` body adds."""

    created_at: AwareDatetime
    display_name: str | None
    parser: str
    extractor: str
    document_checksum: str


class HealthResponse(BaseModel):
    status: Literal["ok"]

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok"}]})


class UploadLimitsResponse(BaseModel):
    max_upload_bytes: int = Field(description="Largest PDF the API accepts, in bytes")

    model_config = ConfigDict(json_schema_extra={"examples": [{"max_upload_bytes": 20971520}]})


class ReadinessResponse(BaseModel):
    status: Literal["ready"]

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ready"}]})


class ReadinessUnavailableDetail(BaseModel):
    status: Literal["not ready"]
    unavailable: list[str]


class ReadinessUnavailableResponse(BaseModel):
    detail: ReadinessUnavailableDetail

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "detail": {
                        "status": "not ready",
                        "unavailable": ["redis", "postgres"],
                    }
                }
            ]
        }
    )


class AcceptedJobResponse(BaseModel):
    """The 202 body from queueing a job. A later read carries the stored record."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    status: Literal["queued"]


class JobResponse(BaseModel):
    """One job. Its ETS rows are on ``GET /api/jobs/{job_id}/ets``.

    ``waiting_for`` names the service the job is waiting for, and ``waiting_since``
    is when that wait began. Both are null unless the stage deferred the job.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    status: Literal["queued", "parsing", "parsed", "extracting", "done"]
    waiting_for: WaitingFor | None
    waiting_since: AwareDatetime | None


class JobFullResponse(JobResponse, JobRecord):
    """One job with the stored record."""


class FailedJobResponse(BaseModel):
    """A failed job. ``failed_stage`` is the status the job had when it failed."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    status: Literal["failed"]
    error: SafeError
    failed_stage: FailedStage


class FailedJobFullResponse(FailedJobResponse, JobRecord):
    """A failed job with the stored record beside the error."""


class EtsRowsResponse(BaseModel):
    """The ETS rows for one done job, in observation order."""

    model_config = ConfigDict(extra="forbid")

    rows: list[EtsRow]


JobStatusResponse = JobResponse | JobFullResponse | FailedJobResponse | FailedJobFullResponse


class DocumentMetadataResponse(BaseModel):
    checksum: Sha256Hex
    display_name: str | None = None
    upload_time: datetime
    size_bytes: int

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                    "display_name": "sample.pdf",
                    "upload_time": "2026-10-26T12:00:00Z",
                    "size_bytes": 1048576,
                }
            ]
        }
    )


class CreateJobRequest(BaseModel):
    # A Literal, so the OpenAPI schema lists the parser names and the frontend's
    # generated types do too.
    parser: ParserName = Field(default=DEFAULT_PARSER_NAME, description="Parser backend name")
    extractor: ExtractorName = Field(
        default=DEFAULT_EXTRACTOR_NAME, description="Extractor backend name"
    )

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "parser": "default",
                    "extractor": "default",
                }
            ]
        },
    )
