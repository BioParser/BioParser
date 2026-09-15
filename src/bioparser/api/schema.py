from typing import Literal

from pydantic import BaseModel


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["queued"]


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
