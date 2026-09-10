import pytest
from fastapi.testclient import TestClient

from bioparser.api import config
from bioparser.api.app import app

# A minimal byte string that passes every /submit validation check.
MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def test_body_limit_middleware_is_installed() -> None:
    names = [middleware.cls.__name__ for middleware in app.user_middleware]
    assert "RequestBodyLimitMiddleware" in names


def test_submit_and_retrieve_job(client: TestClient) -> None:
    response = client.post(
        "/submit",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
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


def test_submit_pdf(client: TestClient) -> None:
    response = client.post(
        "/submit",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_submit_missing_file_part(client: TestClient) -> None:
    response = client.post("/submit")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "missing_file"
    assert detail["message"]


def test_submit_unsupported_content_type(client: TestClient) -> None:
    response = client.post(
        "/submit",
        files={"file": ("notes.txt", b"just some text", "text/plain")},
    )

    assert response.status_code == 415
    detail = response.json()["detail"]
    assert detail["code"] == "unsupported_content_type"
    assert detail["message"]


def test_submit_content_type_with_parameters(client: TestClient) -> None:
    response = client.post(
        "/submit",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf;charset=binary")},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_submit_content_type_case_insensitive(client: TestClient) -> None:
    response = client.post(
        "/submit",
        files={"file": ("sample.pdf", MINIMAL_PDF, "APPLICATION/PDF")},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_submit_empty_file(client: TestClient) -> None:
    response = client.post(
        "/submit",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "empty_file"
    assert detail["message"]


def test_submit_non_pdf_bytes(client: TestClient) -> None:
    response = client.post(
        "/submit",
        files={"file": ("sample.pdf", b"not a pdf at all", "application/pdf")},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_pdf"
    assert detail["message"]


def test_submit_file_too_large(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "get_api_settings", lambda: config.ApiSettings(max_upload_bytes=10))

    response = client.post(
        "/submit",
        files={"file": ("big.pdf", MINIMAL_PDF, "application/pdf")},
    )

    assert response.status_code == 413
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "Content Too Large"


def test_submit_declared_content_length_too_large(client: TestClient) -> None:
    over_limit = config.DEFAULT_MAX_UPLOAD_BYTES + 1
    response = client.post(
        "/submit",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
        headers={"content-length": str(over_limit)},
    )

    assert response.status_code == 413
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "Content Too Large"


def test_submit_invalid_content_length(client: TestClient) -> None:
    response = client.post(
        "/submit",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
        headers={"content-length": "abc"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_content_length"
    assert detail["message"]


def test_submit_negative_content_length(client: TestClient) -> None:
    response = client.post(
        "/submit",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
        headers={"content-length": "-1"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_content_length"
    assert detail["message"]
