from bioparser.jobs import create_job, get_job


def test_create_job_is_queued_and_can_be_retrieved():
    record = create_job()
    assert record["status"] == "queued"
    assert record["job_id"]
    assert get_job(record["job_id"]) == record


def test_get_unknown_job():
    assert get_job("missing") is None


def test_jobs_have_unique_ids():
    first = create_job()
    second = create_job()

    assert first["job_id"] != second["job_id"]
    assert get_job(first["job_id"]) == first
    assert get_job(second["job_id"]) == second
