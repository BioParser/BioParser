from fastapi import FastAPI, HTTPException, UploadFile

from bioparser.jobs import create_job, get_job
from bioparser.uploads import read_upload, require_file, validate_content_type, validate_pdf_content

app = FastAPI()


@app.post("/submit", status_code=202)
async def submit_job(file: UploadFile | None = None) -> dict[str, str]:
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
