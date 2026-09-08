import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from bioparser.services.vllm import VLLMService

from .jobs import create_job, get_job

# TODO: create logger class?
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    service = VLLMService()
    app.state.vllm = service

    try:
        await service.initialize()
        logger.info("connected to vLLM, serving model %s", service.model)
    except Exception:
        logger.exception("vLLM not reachable at startup; will retry on first request")

    try:
        yield
    finally:
        await service.aclose()


app = FastAPI(lifespan=lifespan)


@app.post("/submit", status_code=202)
def submit_job() -> dict[str, str]:
    record = create_job()
    return {"job_id": record["job_id"], "status": record["status"]}


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, str]:
    record = get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "status": record["status"]}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready(request: Request) -> dict[str, Any]:
    """Is vllm ready"""
    service: VLLMService = request.app.state.vllm
    try:
        await service.initialize()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"vLLM unavailable: {exc}") from exc
    return {"status": "ready", "model": service.model}
