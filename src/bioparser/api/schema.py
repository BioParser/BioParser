from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME, EXTRACTORS
from bioparser.hashing import Sha256Hex
from bioparser.jobstate import SafeError
from bioparser.parser_names import DEFAULT_PARSER_NAME, PARSER_BACKENDS


class HealthResponse(BaseModel):
    status: Literal["ok"]

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok"}]})


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


class QueuedJobResponse(BaseModel):
    job_id: UUID
    status: Literal["queued"]


class RunningJobResponse(BaseModel):
    job_id: UUID
    status: Literal["parsing", "extracting"]


class SucceededJobResponse(BaseModel):
    job_id: UUID
    status: Literal["parsed", "done"]


class FailedJobResponse(BaseModel):
    job_id: UUID
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
    parser: str = Field(default=DEFAULT_PARSER_NAME, description="Parser backend name")
    extractor: str = Field(default=DEFAULT_EXTRACTOR_NAME, description="Extractor backend name")

    @field_validator("parser")
    @classmethod
    def validate_parser(cls, value: str) -> str:
        if value not in PARSER_BACKENDS:
            raise ValueError(f"must be one of: {', '.join(PARSER_BACKENDS)}")
        return value

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
