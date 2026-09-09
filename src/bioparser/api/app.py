import asyncio
import contextlib
import hashlib
import os
import socket
import tempfile
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor, TimeoutError
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


@app.post("/submit", status_code=202)
async def submit_job(request: Request, file: UploadFile | None = None) -> dict[str, str]:
    # Redis is not implemented so does nothing yet except validation
    await _validated_upload(request, file)
    record = create_job()
    return {"job_id": record["job_id"], "status": record["status"]}


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, str]:
    record = get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "status": record["status"]}


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
def health() -> dict[str, str]:
    return {"status": "ok"}


def _check_redis(host: str, port: int, timeout: float) -> bool:
    try:
        conn = socket.create_connection((host, port), timeout=timeout)
        conn.close()
        return True
    except OSError:
        return False


def _check_storage(path: str, timeout: float) -> bool:
    def _attempt_write() -> bool:
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
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_attempt_write)
            return future.result(timeout=timeout)
    except TimeoutError:
        return False


@app.get("/ready")
def readiness() -> dict[str, Any]:
    unavailable: list[str] = []

    try:
        timeout = float(os.getenv("DEPENDENCY_CHECK_TIMEOUT", "1.0"))
        if timeout <= 0:
            raise ValueError("timeout must be positive")
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=503,
            detail={"status": "not ready", "unavailable": ["config"]},
        )

    redis_host = os.getenv("REDIS_HOST")
    if redis_host:
        try:
            redis_port = int(os.getenv("REDIS_PORT", "6379"))
            if not (0 < redis_port < 65536):
                raise ValueError("port must be 1-65535")
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=503,
                detail={"status": "not ready", "unavailable": ["config"]},
            )

        if not _check_redis(redis_host, redis_port, timeout):
            unavailable.append("redis")

    storage_path = os.getenv("ARTIFACT_STORAGE_PATH")
    if storage_path and not _check_storage(storage_path, timeout):
        unavailable.append("storage")

    if unavailable:
        raise HTTPException(
            status_code=503,
            detail={"status": "not ready", "unavailable": unavailable},
        )

    return {"status": "ready"}
