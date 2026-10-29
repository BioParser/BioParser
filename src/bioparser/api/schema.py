from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bioparser.jobstate import SafeError


class JobResponseBase(BaseModel):
    job_id: UUID
    created_at: datetime
    file_name: str
    parser: str
    extractor: str
    document_checksum: str
    has_parse_result: bool


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
