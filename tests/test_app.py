from fastapi.testclient import TestClient


def test_submit_and_retrieve_job(client: TestClient) -> None:

    response = client.post(
        "/submit",
        files={"file": ("sample.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert response.status_code == 202
    record = response.json()

    assert record["job_id"]
    assert record["status"] == "queued"

    response = client.get(f"/jobs/{record['job_id']}")

    assert response.status_code == 200
    assert response.json() == record


def test_unknown_job_returns_404(client: TestClient) -> None:
    response = client.get("/jobs/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


def test_submit_pdf(client):
    response = client.post(
        "/submit",
        files={"file": ("sample.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
