from typing import Literal

from pydantic import BaseModel, ConfigDict


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["queued"]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"job_id": "b3f1c2a4-5678-90ab-cdef-1234567890ab", "status": "queued"}]
        }
    )


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
