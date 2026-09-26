from typing import Literal

from pydantic import BaseModel, ConfigDict


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["queued"]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"job_id": "9c6f2a10-5b3e-4d78-a1f2-7e4c8b0d9351", "status": "queued"}]
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
