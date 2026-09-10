from fastapi import FastAPI, HTTPException, Request, UploadFile

from . import config
from .jobs import create_job, get_job
from .uploads import read_upload, require_file, validate_content_type, validate_pdf_content

app = FastAPI()


@app.post("/submit", status_code=202)
async def submit_job(request: Request, file: UploadFile | None = None) -> dict[str, str]:
    file = require_file(file)
    validate_content_type(file)
    content_length = request.headers.get("content-length")
    if (
        content_length is not None
        and int(content_length) > config.get_api_settings().max_upload_bytes
    ):
        raise HTTPException(
            status_code=413,
            detail={
                "code": "file_too_large",
                "message": f"File exceeds the {config.get_api_settings().max_upload_bytes} byte limit",
            },
        )
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
