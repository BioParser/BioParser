from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel

ErrorCode = Literal[
    "missing_file",
    "empty_file",
    "invalid_pdf",
    "unsupported_content_type",
    "invalid_content_length",
    "job_not_found",
    "invalid_job_id",
    "content_too_large",
    "storage_unavailable",
    "redis_unavailable",
    "queue_unavailable",
]


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


MISSING_FILE = ErrorDetail(code="missing_file", message="No file part in the request")
EMPTY_FILE = ErrorDetail(code="empty_file", message="Uploaded file is empty")
INVALID_PDF = ErrorDetail(code="invalid_pdf", message="File does not look like a PDF")
INVALID_CONTENT_LENGTH = ErrorDetail(
    code="invalid_content_length",
    message="Content-Length header must be a non-negative integer",
)
UNSUPPORTED_CONTENT_TYPE = ErrorDetail(
    code="unsupported_content_type",
    message="Only application/pdf uploads are accepted",
)
JOB_NOT_FOUND = ErrorDetail(code="job_not_found", message="Job not found")
INVALID_JOB_ID = ErrorDetail(code="invalid_job_id", message="job_id must be a version 4 UUID")
CONTENT_TOO_LARGE = ErrorDetail(
    code="content_too_large",
    message="Content Too Large",
)

STORAGE_UNAVAILABLE = ErrorDetail(
    code="storage_unavailable",
    message="Artifact storage is unavailable",
)

REDIS_UNAVAILABLE = ErrorDetail(
    code="redis_unavailable",
    message="Job state service is unavailable",
)

QUEUE_UNAVAILABLE = ErrorDetail(code="queue_unavailable", message="Parse queue is unavailable")


def http_error(status_code: int, detail: ErrorDetail) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail.model_dump())


def error_response(*details: ErrorDetail) -> dict[str, object]:
    examples = {
        detail.code: {
            "summary": detail.message,
            "value": ErrorResponse(detail=detail).model_dump(),
        }
        for detail in details
    }
    return {
        "model": ErrorResponse,
        "content": {"application/json": {"examples": examples}},
    }
