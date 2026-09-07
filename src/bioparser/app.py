
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from bioparser.jobs import create_job, get_job
from bioparser.services.vllm import VLLMService

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


# TODO: move this somewhere; bioparser/src/api/routes/schemas?
class GenerateRequest(BaseModel):
    prompt: str
    system_prompt: str | None = None
    temperature: float = 0.0
    max_tokens: int = 1024


# TODO: remove this later; just a temp test end-point
@app.post("/testgenerate")
async def generate(request: Request, body: GenerateRequest) -> dict[str, str]:
    service: VLLMService = request.app.state.vllm
    try:
        response = await service.generate(
                body.prompt,
                system_prompt=body.system_prompt,
                temperature=body.temperature,
                max_tokens=body.max_tokens,
                )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"vLLM error: {exc}") from exc

    return {"response": response}
