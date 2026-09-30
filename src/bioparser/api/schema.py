from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from bioparser.jobstate import SafeError


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
                        "unavailable": ["redis"],
                    }
                }
            ]
        }
    )


class QueuedJobResponse(BaseModel):
    job_id: str
    status: Literal["queued"]


class RunningJobResponse(BaseModel):
    job_id: str
    status: Literal["running"]


class SucceededJobResponse(BaseModel):
    job_id: str
    status: Literal["succeeded"]
    output_artifact_ref: str


class FailedJobResponse(BaseModel):
    job_id: str
    status: Literal["failed"]
    error: SafeError


JobStatusResponse = Annotated[
    QueuedJobResponse | RunningJobResponse | SucceededJobResponse | FailedJobResponse,
    Field(discriminator="status"),
]
