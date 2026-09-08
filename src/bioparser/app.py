from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile

from bioparser.jobs import create_job, get_job

app = FastAPI()


@app.post("/submit", status_code=202)
def submit_job(file: Annotated[UploadFile | None, File()] = None) -> dict[str, str]:

    if file is None:
        raise HTTPException(status_code=400, detail="Missing PDF file")
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="Expected application/pdf")

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
