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


ErrorCode = Literal[
    "missing_file",
    "empty_file",
    "invalid_pdf",
    "unsupported_content_type",
    "invalid_content_length",
    "job_not_found",
]


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail
