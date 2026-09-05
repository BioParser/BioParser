from fastapi.testclient import TestClient


def test_submit_and_retrieve_job(client: TestClient):
    response = client.post("/submit")

    assert response.status_code == 200
    record = response.json()

    assert record["job_id"]
    assert record["status"] == "queued"

    response = client.get(f"/jobs/{record['job_id']}")

    assert response.status_code == 200
    assert response.json() == record


def test_unknown_job_returns_404(client):
    response = client.get("/jobs/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}
