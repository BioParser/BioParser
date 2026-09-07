import uuid

# TODO: After sprint 0, replace with Redis job store (see architecture.md)
# The current dict is not preserved over reboot and other processes cannot see it

JobRecord = dict[str, str]  # job_id, status
_jobs: dict[str, JobRecord] = {}


def create_job() -> JobRecord:
    job_id = str(uuid.uuid4())
    record = {"job_id": job_id, "status": "queued"}
    _jobs[job_id] = record
    return record


def get_job(job_id: str) -> JobRecord | None:
    return _jobs.get(job_id)


# TODO: On later worker implementation:
# A function (update_job_status) is needed to transfer job from "queued" -> "running" -> "success/failed"
# As of now, nothing changes the status
