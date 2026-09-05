from fastapi import FastAPI, HTTPException

from bioparser.jobs import create_job, get_job

app = FastAPI()


@app.post("/submit")
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
