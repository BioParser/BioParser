from fastapi import FastAPI, HTTPException, Request, UploadFile
from starlette.middleware.body_limit import RequestBodyLimitMiddleware
from starlette.responses import PlainTextResponse

from . import config
from .jobs import create_job, get_job
from .uploads import (
    CONTENT_TOO_LARGE,
    read_upload,
    require_file,
    validate_content_length,
    validate_content_type,
    validate_pdf_content,
)

app = FastAPI()
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_size=config.get_api_settings().max_upload_bytes,
)


@app.exception_handler(413)
async def content_too_large_handler(_request: Request, _exc: Exception) -> PlainTextResponse:
    return PlainTextResponse(CONTENT_TOO_LARGE, status_code=413)


@app.post("/submit", status_code=202)
async def submit_job(request: Request, file: UploadFile | None = None) -> dict[str, str]:
    validate_content_length(request.headers.get("content-length"))
    file = require_file(file)
    validate_content_type(file)
    content = await read_upload(file)
    validate_pdf_content(content)
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
