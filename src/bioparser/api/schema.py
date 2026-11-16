from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME, EXTRACTORS
from bioparser.hashing import Sha256Hex
from bioparser.jobs import SafeError
from bioparser.parser_names import DEFAULT_PARSER_NAME, ParserName


class JobResponseBase(BaseModel):
    job_id: UUID
    created_at: AwareDatetime
    display_name: str | None
    parser: str
    extractor: str
    document_checksum: str
    has_parse_result: bool


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


class QueuedJobResponse(JobResponseBase):
    status: Literal["queued"]


class RunningJobResponse(JobResponseBase):
    status: Literal["parsing", "extracting"]


class SucceededJobResponse(JobResponseBase):
    status: Literal["parsed", "done"]


class FailedJobResponse(JobResponseBase):
    status: Literal["failed"]
    error: SafeError


JobStatusResponse = Annotated[
    QueuedJobResponse | RunningJobResponse | SucceededJobResponse | FailedJobResponse,
    Field(discriminator="status"),
]


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
    extractor: str = Field(default=DEFAULT_EXTRACTOR_NAME, description="Extractor backend name")

    @field_validator("extractor")
    @classmethod
    def validate_extractor(cls, value: str) -> str:
        if value not in EXTRACTORS:
            raise ValueError(f"must be one of: {', '.join(EXTRACTORS)}")
        return value

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
