import os
import socket
import tempfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any

from fastapi import FastAPI, HTTPException

from .jobs import create_job, get_job

app = FastAPI()


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
