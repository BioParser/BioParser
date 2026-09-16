import asyncio
import contextlib
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, UploadFile
from starlette.middleware.body_limit import RequestBodyLimitMiddleware
from starlette.responses import PlainTextResponse

from bioparser.services.mineru import MinerUClient
from bioparser.services.vllm import VLLMService

from . import config
from .jobs import create_job, get_job
from .pipeline import PipelineError, run_pipeline
from .schema import ErrorDetail, ErrorResponse, HealthResponse, JobStatusResponse
from .uploads import (
    CONTENT_TOO_LARGE,
    read_upload,
    require_file,
    validate_content_length,
    validate_content_type,
    validate_pdf_content,
)

# TODO: logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = config.get_api_settings()
    app.state.mineru = MinerUClient(
        settings.mineru_base_url,
        settings.mineru_timeout_seconds,
        settings.mineru_connect_timeout_seconds,
    )
    app.state.vllm = VLLMService()
    # Find served model id early to log what we are using
    with contextlib.suppress(Exception):  # TODO: log this once there is a logger
        await app.state.vllm.get_model()
    # Bounds /extract only. The queue replaces this once the worker lands.
    app.state.extract_sem = asyncio.Semaphore(settings.extract_concurrency)
    try:
        yield
    finally:
        for client in (
            getattr(app.state, "mineru", None),
            getattr(app.state, "vllm", None),
        ):
            if client is not None:
                await client.aclose()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_size=config.get_api_settings().max_upload_bytes,
)


@app.exception_handler(413)
async def content_too_large_handler(_request: Request, _exc: Exception) -> PlainTextResponse:
    return PlainTextResponse(CONTENT_TOO_LARGE, status_code=413)


async def _validated_upload(request: Request, file: UploadFile | None) -> tuple[str, bytes]:
    validate_content_length(request.headers.get("content-length"))
    file = require_file(file)
    validate_content_type(file)
    content = await read_upload(file)
    validate_pdf_content(content)
    # If we use sha256 as digest then same files get same digest
    # Can be used as job id etc
    return hashlib.sha256(content).hexdigest(), content


@asynccontextmanager
async def _extract_slot(app: FastAPI) -> AsyncIterator[None]:
    """Used for /extract sync runs

    /submit should use async runs when redis implemented
    """
    timeout = config.get_api_settings().extract_queue_timeout_seconds
    try:
        async with asyncio.timeout(timeout):
            await app.state.extract_sem.acquire()
    except TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail="Pipeline is saturated, retry shortly",
            headers={"Retry-After": "30"},
        ) from exc
    try:
        yield
    finally:
        app.state.extract_sem.release()


@app.post(
    "/submit",
    status_code=202,
    responses={
        400: {
            "model": ErrorResponse,
            "content": {
                "application/json": {
                    "examples": {
                        "missing_file": {
                            "summary": "No file part in the request",
                            "value": {
                                "detail": {
                                    "code": "missing_file",
                                    "message": "No file part in the request",
                                }
                            },
                        },
                        "empty_file": {
                            "summary": "Uploaded file is empty",
                            "value": {
                                "detail": {
                                    "code": "empty_file",
                                    "message": "Uploaded file is empty",
                                }
                            },
                        },
                        "invalid_pdf": {
                            "summary": "File does not look like a PDF",
                            "value": {
                                "detail": {
                                    "code": "invalid_pdf",
                                    "message": "File does not look like a PDF",
                                }
                            },
                        },
                        "invalid_content_length": {
                            "summary": "Content-Length header must be a non-negative integer",
                            "value": {
                                "detail": {
                                    "code": "invalid_content_length",
                                    "message": "Content-Length header must be a non-negative integer",
                                }
                            },
                        },
                    }
                }
            },
        },
        415: {
            "model": ErrorResponse,
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "code": "unsupported_content_type",
                            "message": "Only application/pdf uploads are accepted",
                        }
                    }
                }
            },
        },
    },
)
async def submit_job(request: Request, file: UploadFile | None = None) -> JobStatusResponse:
    # Redis is not implemented so does nothing yet except validation
    await _validated_upload(request, file)
    record = create_job()
    return JobStatusResponse(job_id=record.job_id, status="queued")


@app.get(
    "/jobs/{job_id}",
    responses={
        404: {
            "model": ErrorResponse,
            "content": {
                "application/json": {
                    "example": {"detail": {"code": "job_not_found", "message": "Job not found"}}
                }
            },
        }
    },
)
def job_status(job_id: str) -> JobStatusResponse:
    record = get_job(job_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorDetail(code="job_not_found", message="Job not found").model_dump(),
        )
    return JobStatusResponse(job_id=job_id, status=record.status)


@app.post("/extract")
async def extract(request: Request, file: UploadFile | None = None) -> dict[str, Any]:
    """Runs validate -> mineru -> mapper -> vLLM pipeline for testing,
    synchronously"""
    checksum, content = await _validated_upload(request, file)
    settings = config.get_api_settings()
    async with _extract_slot(request.app):
        try:
            return await run_pipeline(
                mineru=request.app.state.mineru,
                vllm=request.app.state.vllm,
                checksum=checksum,
                content=content,
                char_budget=settings.extraction_char_budget,
                max_tokens=settings.extraction_max_tokens,
            )
        except PipelineError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/health")
def health() -> HealthResponse:
    return HealthResponse(status="ok")
