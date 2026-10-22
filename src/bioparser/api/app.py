import asyncio
import contextlib
import hashlib
import json
import logging
import os
import socket
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, UploadFile
from pydantic import ValidationError

from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME
from bioparser.jobqueue import (
    JobQueueError,
    ParseJobMessage,
)
from bioparser.jobqueue.dramatiq import DramatiqJobQueue
from bioparser.jobstate import (
    CorruptJobStateError,
    JobState,
    JobStateError,
    RedisJobStateStore,
    SafeError,
)
from bioparser.logging_config import error_fields, redact_url, setup_logging
from bioparser.parser import ParserArtifact
from bioparser.parser_names import DEFAULT_PARSER_NAME
from bioparser.services.mineru import MinerUClient
from bioparser.services.vllm import VLLMService
from bioparser.services.vllm.config import get_settings as get_vllm_settings
from bioparser.services.vllm.vllm import VLLMError
from bioparser.storage import (
    ArtifactAlreadyExistsError,
    ArtifactMetadata,
    ArtifactNotFoundError,
    CreationInfo,
    FileSystemArtifactStorage,
    StorageError,
    StoragePayloadError,
)

from . import config
from .errors import (
    ARTIFACT_INVALID,
    CONTENT_TOO_LARGE,
    EMPTY_FILE,
    INVALID_CONTENT_LENGTH,
    INVALID_JOB_ID,
    INVALID_PDF,
    JOB_NOT_FOUND,
    JOB_STATE_UNREADABLE,
    MISSING_FILE,
    QUEUE_UNAVAILABLE,
    REDIS_UNAVAILABLE,
    STORAGE_UNAVAILABLE,
    UNSUPPORTED_CONTENT_TYPE,
    error_response,
    http_error,
)
from .middleware import TypedRequestBodyLimitMiddleware
from .pipeline import PipelineError, run_pipeline
from .schema import (
    FailedJobResponse,
    HealthResponse,
    JobStatusResponse,
    QueuedJobResponse,
    ReadinessResponse,
    ReadinessUnavailableResponse,
    RunningJobResponse,
    SucceededJobResponse,
)
from .uploads import (
    read_upload,
    require_file,
    validate_content_length,
    validate_content_type,
    validate_pdf_content,
)

LOGGER = logging.getLogger(__name__)


class LifespanError(RuntimeError):
    """Startup or shutdown failed."""


@contextlib.asynccontextmanager
async def _failure_logged_redacted(phase: str) -> AsyncIterator[None]:
    try:
        yield
    except Exception as exc:
        LOGGER.critical(
            "lifespan failed", exc_info=True, extra={"phase": phase, **error_fields(exc)}
        )
        raise LifespanError(f"{phase} failed: {type(exc).__name__}") from None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        async with _failure_logged_redacted("startup"):
            settings = config.get_api_settings()
            setup_logging(settings.log_level)
            if settings.redis_url is None:
                raise RuntimeError("BIOPARSER_REDIS_URL is required")
            if settings.artifact_storage_path is None:
                raise RuntimeError("BIOPARSER_ARTIFACT_STORAGE_PATH is required")

            app.state.mineru = MinerUClient(
                settings.mineru_base_url,
                settings.mineru_timeout_seconds,
                settings.mineru_connect_timeout_seconds,
                tier=settings.mineru_tier,
            )

            app.state.vllm = VLLMService()
            vllm_base_url = redact_url(get_vllm_settings().vllm_base_url)
            try:
                model = await app.state.vllm.get_model()
            except Exception as exc:
                LOGGER.warning(
                    "vllm model discovery failed",
                    exc_info=not isinstance(exc, VLLMError),
                    extra={"vllm_base_url": vllm_base_url, **error_fields(exc)},
                )
            else:
                LOGGER.info(
                    "vllm model discovered",
                    extra={"vllm_base_url": vllm_base_url, "model": model},
                )

            redis_url = str(settings.redis_url)
            redis_timeout = settings.redis_timeout_seconds
            app.state.job_store = RedisJobStateStore(
                redis_url,
                operation_timeout_seconds=redis_timeout,
            )
            app.state.parse_queue = DramatiqJobQueue.from_redis(
                redis_url,
                name=settings.parse_queue_name,
                model=ParseJobMessage,
                timeout_s=redis_timeout,
            )

            app.state.artifact_storage = FileSystemArtifactStorage(
                root_path=settings.artifact_storage_path
            )

            # Bounds /extract only. The queue replaces this once the worker lands.
            app.state.extract_sem = asyncio.Semaphore(settings.extract_concurrency)

        yield
    finally:
        async with _failure_logged_redacted("shutdown"):
            for client in (
                getattr(app.state, "mineru", None),
                getattr(app.state, "vllm", None),
                getattr(app.state, "job_store", None),
            ):
                if client is not None:
                    await client.aclose()


# Uvicorn imports the app before emitting its first log line.
setup_logging()
try:
    setup_logging(config.get_api_settings().log_level)
except ValidationError as exc:
    LOGGER.critical(
        "invalid configuration", extra={"invalid_settings": config.invalid_settings(exc)}
    )
    raise

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    TypedRequestBodyLimitMiddleware,
    max_body_size=config.get_api_settings().max_upload_bytes,
)


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

    /api/extractions should use async runs when redis implemented
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


def _job_state_response(state: JobState) -> JobStatusResponse:
    if state.status == "queued":
        return QueuedJobResponse(
            job_id=state.job_id,
            status="queued",
        )

    if state.status in ("parsing", "extracting"):
        return RunningJobResponse(
            job_id=state.job_id,
            status=state.status,
        )

    if state.status in ("parsed", "done"):
        if state.parse_result_ref is None:
            raise ValueError("Parsed job is missing parse_result_ref")

        return SucceededJobResponse(
            job_id=state.job_id,
            status=state.status,
            output_artifact_ref=state.parse_result_ref,
        )

    if state.status == "failed":
        if state.error is None:
            raise ValueError("Failed job is missing error")

        return FailedJobResponse(
            job_id=state.job_id,
            status="failed",
            error=state.error,
        )
    else:
        raise ValueError(f"Unknown job status: {state.status}")


@app.post(
    "/api/extractions",
    status_code=202,
    responses={
        400: error_response(MISSING_FILE, EMPTY_FILE, INVALID_PDF, INVALID_CONTENT_LENGTH),
        413: error_response(CONTENT_TOO_LARGE),
        415: error_response(UNSUPPORTED_CONTENT_TYPE),
        500: error_response(JOB_STATE_UNREADABLE),
        503: error_response(STORAGE_UNAVAILABLE, REDIS_UNAVAILABLE, QUEUE_UNAVAILABLE),
    },
)
async def submit_job(request: Request, file: UploadFile | None = None) -> QueuedJobResponse:
    document_id, pdf_bytes = await _validated_upload(request, file)
    job_id = uuid.uuid4()
    creation_info = CreationInfo(created_by="api-upload")
    artifact_metadata = ArtifactMetadata(
        artifact_path=f"pdf/{document_id}",
        media_type="application/pdf",
        creation_info=creation_info,
    )

    fields = {
        "job_id": str(job_id),
        "document_id": document_id,
    }

    try:
        pdf_ref = await asyncio.to_thread(
            request.app.state.artifact_storage.store, pdf_bytes, artifact_metadata
        )
    except ArtifactAlreadyExistsError:
        pdf_ref = artifact_metadata.artifact_path
    except StorageError as exc:
        LOGGER.error("artifact store failed", extra=fields | error_fields(exc))
        raise http_error(503, STORAGE_UNAVAILABLE) from exc

    job_state = JobState(
        job_id=job_id,
        status="queued",
        parser=DEFAULT_PARSER_NAME,
        extractor=DEFAULT_EXTRACTOR_NAME,
        pdf_ref=pdf_ref,
    )
    try:
        await request.app.state.job_store.create(job_state)
    except JobStateError as exc:
        LOGGER.error("job state create failed", extra=fields | error_fields(exc))
        raise http_error(503, REDIS_UNAVAILABLE) from exc

    parse_job_message = ParseJobMessage(
        job_id=job_id,
    )
    try:
        await asyncio.to_thread(request.app.state.parse_queue.submit, parse_job_message)
    except JobQueueError as exc:
        LOGGER.error("parse job enqueue failed", extra=fields | error_fields(exc))
        failed_state = job_state.with_changes(
            status="failed",
            error=SafeError(code="internal_error"),
        )
        with contextlib.suppress(JobStateError):
            await request.app.state.job_store.update(failed_state)
        raise http_error(503, QUEUE_UNAVAILABLE) from exc

    return QueuedJobResponse(job_id=job_id, status="queued")


@app.get(
    "/api/jobs/{job_id}",
    responses={
        404: error_response(JOB_NOT_FOUND),
        422: error_response(INVALID_JOB_ID),
        500: error_response(JOB_STATE_UNREADABLE, ARTIFACT_INVALID),
        503: error_response(
            REDIS_UNAVAILABLE,
            STORAGE_UNAVAILABLE,
        ),
    },
)
async def job_status(request: Request, job_id: str) -> JobStatusResponse:
    try:
        parsed_job_id = UUID(job_id)
    except ValueError as exc:
        raise http_error(422, INVALID_JOB_ID) from exc
    if parsed_job_id.version != 4:
        raise http_error(422, INVALID_JOB_ID)
    try:
        record = await request.app.state.job_store.get(parsed_job_id)

    except CorruptJobStateError as exc:
        LOGGER.error("job state unreadable", extra={"job_id": str(job_id), **error_fields(exc)})
        raise http_error(500, JOB_STATE_UNREADABLE) from exc

    except JobStateError as exc:
        LOGGER.warning("job state read failed", extra={"job_id": str(job_id), **error_fields(exc)})
        raise http_error(503, REDIS_UNAVAILABLE) from exc

    if record is None:
        raise http_error(404, JOB_NOT_FOUND)
    if record.status in ("parsed", "done"):
        if record.parse_result_ref is None:
            LOGGER.error("parser artifact invalid", extra={"job_id": str(job_id)})
            raise http_error(500, ARTIFACT_INVALID)
        artifact_fields = {
            "job_id": str(job_id),
            "artifact_ref": str(record.parse_result_ref),
        }
        try:
            payload = await asyncio.to_thread(
                request.app.state.artifact_storage.retrieve,
                record.parse_result_ref,
            )
        except (ArtifactNotFoundError, StoragePayloadError) as exc:
            LOGGER.error(
                "parser artifact missing or invalid",
                extra=artifact_fields | error_fields(exc),
            )
            raise http_error(500, ARTIFACT_INVALID) from exc
        except StorageError as exc:
            LOGGER.warning("parser artifact read failed", extra=artifact_fields | error_fields(exc))
            raise http_error(503, STORAGE_UNAVAILABLE) from exc
        try:
            ParserArtifact.model_validate(json.loads(payload))
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            LOGGER.error("parser artifact invalid", extra=artifact_fields | error_fields(exc))
            raise http_error(500, ARTIFACT_INVALID) from exc
    return _job_state_response(record)


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
                tier=settings.mineru_tier,
                char_budget=settings.extraction_char_budget,
                max_tokens=settings.extraction_max_tokens,
            )
        except PipelineError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/health")
def health() -> HealthResponse:
    return HealthResponse(status="ok")


async def _check_redis(redis_url: str, timeout: float) -> bool:
    def _connect_to_redis() -> bool:
        parsed = urlparse(redis_url)
        host = parsed.hostname
        port = parsed.port or 6379
        if not host:
            return False
        try:
            conn = socket.create_connection((host, port), timeout=timeout)
            conn.close()
            return True
        except OSError:
            return False

    try:
        return await asyncio.wait_for(asyncio.to_thread(_connect_to_redis), timeout=timeout)
    except TimeoutError:
        return False


async def _check_storage(path: str, timeout: float) -> bool:
    def _attempt_storage_write() -> bool:
        try:
            if not os.path.isdir(path):
                return False
            with tempfile.NamedTemporaryFile(dir=path, delete=True) as tmp:
                tmp.write(b"readiness")
                tmp.flush()
            return True
        except OSError:
            return False

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_attempt_storage_write),
            timeout=timeout,
        )
    except TimeoutError:
        return False


@app.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={
        503: {
            "model": ReadinessUnavailableResponse,
            "description": "One or more dependencies are unavailable or misconfigured",
        }
    },
)
async def readiness() -> ReadinessResponse:
    unavailable: list[str] = []

    try:
        settings = config.get_api_settings()
    except ValidationError as exc:
        raise HTTPException(
            status_code=503,
            detail={"status": "not ready", "unavailable": ["config"]},
        ) from exc

    timeout = settings.dependency_check_timeout_seconds

    if settings.redis_url and not await _check_redis(str(settings.redis_url), timeout):
        unavailable.append("redis")

    if settings.artifact_storage_path and not await _check_storage(
        settings.artifact_storage_path,
        timeout,
    ):
        unavailable.append("storage")

    if unavailable:
        raise HTTPException(
            status_code=503,
            detail={"status": "not ready", "unavailable": unavailable},
        )

    return ReadinessResponse(status="ready")
