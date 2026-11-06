import asyncio
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from stubs import (
    StubArtifactStorage,
    StubJobs,
    StubParseQueue,
)

from bioparser.api.app import app
from bioparser.api.schema import DocumentMetadataResponse
from bioparser.db.documents import DocumentRecord
from bioparser.db.engine import DatabaseError
from bioparser.hashing import sha256_bytes
from bioparser.jobs.queue import JobQueueError
from bioparser.storage import ArtifactMetadata, StorageError

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
MINIMAL_PDF_HASH = sha256_bytes(MINIMAL_PDF)

SECOND_PDF = b"%PDF-1.4\n2 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
SECOND_PDF_HASH = sha256_bytes(SECOND_PDF)


def test_upload_document_creates_new_document(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    artifact_storage, _, _, events = stub_api_adapters

    response = client.post(
        "/api/documents",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
    )

    assert response.status_code == 201
    doc = DocumentMetadataResponse.model_validate(response.json())
    assert doc.checksum == MINIMAL_PDF_HASH
    assert doc.display_name == "sample.pdf"
    assert doc.size_bytes == len(MINIMAL_PDF)
    assert doc.upload_time.tzinfo is not None

    stored_ref = f"pdf/{MINIMAL_PDF_HASH}"
    assert stored_ref in artifact_storage.artifacts
    stored_bytes, _ = artifact_storage.artifacts[stored_ref]
    assert stored_bytes == MINIMAL_PDF
    assert "artifact.store" in events
    assert "document.create" in events


def test_upload_document_returns_200_if_already_stored(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    _ = stub_api_adapters

    # First upload (201)
    res1 = client.post(
        "/api/documents",
        files={"file": ("paper.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert res1.status_code == 201

    # Second upload with same PDF content (200)
    res2 = client.post(
        "/api/documents",
        files={"file": ("different_name.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert res2.status_code == 200
    doc = DocumentMetadataResponse.model_validate(res2.json())
    assert doc.checksum == MINIMAL_PDF_HASH
    # Preserves original metadata display_name
    assert doc.display_name == "paper.pdf"
    assert doc.size_bytes == len(MINIMAL_PDF)


def test_upload_document_validation_errors(client: TestClient) -> None:
    # Missing file
    res = client.post("/api/documents")
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "missing_file"

    # Empty file
    res = client.post("/api/documents", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "empty_file"

    # Invalid PDF magic
    res = client.post(
        "/api/documents", files={"file": ("bad.pdf", b"not a pdf", "application/pdf")}
    )
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "invalid_pdf"

    # Unsupported content type
    res = client.post("/api/documents", files={"file": ("text.txt", MINIMAL_PDF, "text/plain")})
    assert res.status_code == 415
    assert res.json()["detail"]["code"] == "unsupported_content_type"


def test_upload_document_storage_error(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    artifact_storage, _, _, _ = stub_api_adapters
    artifact_storage.store_error = StorageError("Disk full")

    response = client.post(
        "/api/documents",
        files={"file": ("paper.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "storage_unavailable"


def test_list_documents_empty_and_populated(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    _ = stub_api_adapters

    # Initially empty
    res = client.get("/api/documents")
    assert res.status_code == 200
    assert res.json() == []

    # Store 2 documents
    client.post(
        "/api/documents",
        files={"file": ("first.pdf", MINIMAL_PDF, "application/pdf")},
    )
    client.post(
        "/api/documents",
        files={"file": ("second.pdf", SECOND_PDF, "application/pdf")},
    )

    res = client.get("/api/documents")
    assert res.status_code == 200
    docs = [DocumentMetadataResponse.model_validate(item) for item in res.json()]
    assert len(docs) == 2
    checksums = {d.checksum for d in docs}
    assert checksums == {MINIMAL_PDF_HASH, SECOND_PDF_HASH}


def test_list_documents_database_error(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    _, jobs, _, _ = stub_api_adapters
    jobs.doc_get_error = DatabaseError("Failed to list")

    res = client.get("/api/documents")
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "database_unavailable"


def test_get_document_metadata_success(
    client: TestClient,
) -> None:
    client.post(
        "/api/documents",
        files={"file": ("paper.pdf", MINIMAL_PDF, "application/pdf")},
    )

    res = client.get(f"/api/documents/{MINIMAL_PDF_HASH}")
    assert res.status_code == 200
    doc = DocumentMetadataResponse.model_validate(res.json())
    assert doc.checksum == MINIMAL_PDF_HASH
    assert doc.display_name == "paper.pdf"
    assert doc.size_bytes == len(MINIMAL_PDF)


def test_get_document_not_found(client: TestClient) -> None:
    fake_hash = "f" * 64
    res = client.get(f"/api/documents/{fake_hash}")
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "document_not_found"


def test_get_document_invalid_checksum(client: TestClient) -> None:
    res = client.get("/api/documents/not-a-valid-checksum")
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "invalid_checksum"


def test_get_document_file_success(
    client: TestClient,
) -> None:
    client.post(
        "/api/documents",
        files={"file": ("research.pdf", MINIMAL_PDF, "application/pdf")},
    )

    res = client.get(f"/api/documents/{MINIMAL_PDF_HASH}/file")
    assert res.status_code == 200
    assert res.content == MINIMAL_PDF
    assert res.headers["content-type"] == "application/pdf"
    assert 'filename="research.pdf"' in res.headers.get("content-disposition", "")
    assert "immutable" in res.headers.get("cache-control", "")


def test_get_document_file_fallback_filename(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    artifact_storage, jobs, _, _ = stub_api_adapters
    meta = ArtifactMetadata(
        artifact_path=f"pdf/{MINIMAL_PDF_HASH}",
        media_type="application/pdf",
    )
    asyncio.run(artifact_storage.store(MINIMAL_PDF, meta))
    jobs.documents[MINIMAL_PDF_HASH] = DocumentRecord(
        checksum=MINIMAL_PDF_HASH,
        display_name=None,
        size_bytes=len(MINIMAL_PDF),
        created_at=datetime.now(UTC),
    )

    res = client.get(f"/api/documents/{MINIMAL_PDF_HASH}/file")
    assert res.status_code == 200
    assert f'filename="{MINIMAL_PDF_HASH}.pdf"' in res.headers.get("content-disposition", "")


def test_get_document_file_sanitizes_filename(
    client: TestClient,
) -> None:
    bad_name = "malicious\"header\r\ninjection'test.pdf"
    client.post(
        "/api/documents",
        files={"file": (bad_name, MINIMAL_PDF, "application/pdf")},
    )

    meta_res = client.get(f"/api/documents/{MINIMAL_PDF_HASH}")
    assert meta_res.status_code == 200
    assert meta_res.json()["display_name"] == "maliciousheaderinjectiontest.pdf"

    res = client.get(f"/api/documents/{MINIMAL_PDF_HASH}/file")
    assert res.status_code == 200
    disposition = res.headers.get("content-disposition", "")
    assert 'filename="maliciousheaderinjectiontest.pdf"' in disposition
    assert "filename*=UTF-8''maliciousheaderinjectiontest.pdf" in disposition
    assert "\r" not in disposition
    assert "\n" not in disposition


def test_get_document_file_unicode_filename(
    client: TestClient,
) -> None:
    client.post(
        "/api/documents",
        files={"file": ("käsikirja.pdf", MINIMAL_PDF, "application/pdf")},
    )

    res = client.get(f"/api/documents/{MINIMAL_PDF_HASH}/file")
    assert res.status_code == 200
    disposition = res.headers.get("content-disposition", "")
    assert 'filename="kasikirja.pdf"' in disposition
    assert "filename*=UTF-8''k%C3%A4sikirja.pdf" in disposition


def test_get_document_file_not_found_and_invalid_checksum(client: TestClient) -> None:
    res = client.get(f"/api/documents/{'0' * 64}/file")
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "document_not_found"

    res = client.get("/api/documents/invalid/file")
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "invalid_checksum"


def test_extract_stores_the_document_and_queues_a_job(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    artifact_storage, job_store, parse_queue, _events = stub_api_adapters

    res = client.post(
        "/api/extractions",
        files={"file": ("paper.pdf", MINIMAL_PDF, "application/pdf")},
    )

    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "queued"
    assert set(body) == {"job_id", "status"}
    state = job_store.states[body["job_id"]]
    assert state.parser == "default"
    assert state.extractor == "default"
    assert state.pdf_ref == f"pdf/{MINIMAL_PDF_HASH}"
    assert artifact_storage.artifacts[state.pdf_ref][0] == MINIMAL_PDF
    assert parse_queue.messages[0].job_id == UUID(body["job_id"])
    stored = client.get(f"/api/documents/{MINIMAL_PDF_HASH}")
    assert stored.status_code == 200
    assert stored.json()["checksum"] == MINIMAL_PDF_HASH
    assert stored.json()["display_name"] == "paper.pdf"
    assert stored.json()["size_bytes"] == len(MINIMAL_PDF)


def test_extract_of_the_same_pdf_creates_another_job(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    _artifact_storage, job_store, parse_queue, _events = stub_api_adapters

    responses = [
        client.post(
            "/api/extractions", files={"file": ("paper.pdf", MINIMAL_PDF, "application/pdf")}
        )
        for _ in range(2)
    ]

    assert [response.status_code for response in responses] == [202, 202]
    assert len(job_store.states) == 2
    assert len(parse_queue.messages) == 2
    listed = client.get("/api/documents")
    assert listed.status_code == 200
    assert [doc["checksum"] for doc in listed.json()] == [MINIMAL_PDF_HASH]


def test_extract_keeps_the_existing_display_name(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    _artifact_storage, job_store, _parse_queue, _events = stub_api_adapters

    first = client.post(
        "/api/extractions",
        files={"file": ("paper.pdf", MINIMAL_PDF, "application/pdf")},
    )
    second = client.post(
        "/api/extractions",
        files={"file": ("other.pdf", MINIMAL_PDF, "application/pdf")},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert len(job_store.states) == 2
    stored = client.get(f"/api/documents/{MINIMAL_PDF_HASH}")
    assert stored.status_code == 200
    assert stored.json()["display_name"] == "paper.pdf"


def test_a_failed_extraction_insert_stores_no_job(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    _artifact_storage, job_store, _parse_queue, _events = stub_api_adapters
    job_store.create_error = DatabaseError("insert failed")

    res = client.post(
        "/api/extractions",
        files={"file": ("paper.pdf", MINIMAL_PDF, "application/pdf")},
    )

    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "database_unavailable"
    assert job_store.states == {}
    assert job_store.documents == {}


def test_a_failed_extraction_insert_keeps_an_existing_document(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    _artifact_storage, job_store, _parse_queue, _events = stub_api_adapters
    created = client.post(
        "/api/documents",
        files={"file": ("paper.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert created.status_code == 201
    job_store.create_error = DatabaseError("insert failed")

    res = client.post(
        "/api/extractions",
        files={"file": ("other.pdf", MINIMAL_PDF, "application/pdf")},
    )

    assert res.status_code == 503
    assert job_store.states == {}
    stored = client.get(f"/api/documents/{MINIMAL_PDF_HASH}")
    assert stored.status_code == 200
    assert stored.json()["display_name"] == "paper.pdf"


def test_submit_document_job_success(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    _, job_store, parse_queue, _ = stub_api_adapters

    client.post(
        "/api/documents",
        files={"file": ("paper.pdf", MINIMAL_PDF, "application/pdf")},
    )

    res = client.post(
        f"/api/documents/{MINIMAL_PDF_HASH}/jobs",
        json={"parser": "mineru", "extractor": "default"},
    )
    assert res.status_code == 202
    data = res.json()
    assert data["status"] == "queued"
    assert set(data) == {"job_id", "status"}
    job_id = UUID(data["job_id"])

    # Job record created in store
    assert str(job_id) in job_store.states
    state = job_store.states[str(job_id)]
    assert state.parser == "mineru"
    assert state.extractor == "default"
    assert state.pdf_ref == f"pdf/{MINIMAL_PDF_HASH}"

    # Parse message queued
    assert len(parse_queue.messages) == 1
    assert parse_queue.messages[0].job_id == job_id


def test_submit_document_job_default_parameters(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    _, job_store, _, _ = stub_api_adapters

    client.post(
        "/api/documents",
        files={"file": ("paper.pdf", MINIMAL_PDF, "application/pdf")},
    )

    # Empty json body {}
    res = client.post(f"/api/documents/{MINIMAL_PDF_HASH}/jobs", json={})
    assert res.status_code == 202
    job_id = res.json()["job_id"]
    state = job_store.states[job_id]
    assert state.parser == "default"
    assert state.extractor == "default"


def test_submit_document_job_document_not_found(client: TestClient) -> None:
    res = client.post(f"/api/documents/{'0' * 64}/jobs", json={})
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "document_not_found"


def test_submit_document_job_invalid_backend(
    client: TestClient,
) -> None:
    client.post(
        "/api/documents",
        files={"file": ("paper.pdf", MINIMAL_PDF, "application/pdf")},
    )

    res = client.post(
        f"/api/documents/{MINIMAL_PDF_HASH}/jobs",
        json={"parser": "invalid_parser_name"},
    )
    assert res.status_code == 422


def test_submit_document_job_queue_error_marks_failed(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    _, job_store, parse_queue, _ = stub_api_adapters
    parse_queue.submit_error = JobQueueError("Queue unavailable")

    client.post(
        "/api/documents",
        files={"file": ("paper.pdf", MINIMAL_PDF, "application/pdf")},
    )

    res = client.post(f"/api/documents/{MINIMAL_PDF_HASH}/jobs", json={})
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "queue_unavailable"

    failed_state = next(s for s in job_store.states.values() if s.status == "failed")
    assert failed_state.error_code == "internal_error"


def test_list_document_jobs(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    _ = stub_api_adapters
    # Upload doc 1 and doc 2
    client.post(
        "/api/documents",
        files={"file": ("paper1.pdf", MINIMAL_PDF, "application/pdf")},
    )
    client.post(
        "/api/documents",
        files={"file": ("paper2.pdf", SECOND_PDF, "application/pdf")},
    )

    # Initially doc 1 has no jobs
    res = client.get(f"/api/documents/{MINIMAL_PDF_HASH}/jobs")
    assert res.status_code == 200
    assert res.json() == []

    # Launch 2 jobs for doc 1, 1 job for doc 2
    j1 = client.post(f"/api/documents/{MINIMAL_PDF_HASH}/jobs", json={"parser": "default"}).json()
    j2 = client.post(f"/api/documents/{MINIMAL_PDF_HASH}/jobs", json={"parser": "mineru"}).json()
    j3 = client.post(f"/api/documents/{SECOND_PDF_HASH}/jobs", json={"parser": "default"}).json()

    res1 = client.get(f"/api/documents/{MINIMAL_PDF_HASH}/jobs", params={"detail": "full"})
    assert res1.status_code == 200
    document_jobs = res1.json()
    job_ids_1 = {j["job_id"] for j in document_jobs}
    assert job_ids_1 == {j1["job_id"], j2["job_id"]}
    assert all(j["document_checksum"] == MINIMAL_PDF_HASH for j in document_jobs)
    assert all(j["display_name"] == "paper1.pdf" for j in document_jobs)
    assert all("has_parse_result" not in j for j in document_jobs)
    assert all("rows" not in j for j in document_jobs)
    assert all(datetime.fromisoformat(j["created_at"]).tzinfo is not None for j in document_jobs)

    res2 = client.get(f"/api/documents/{SECOND_PDF_HASH}/jobs", params={"detail": "full"})
    assert res2.status_code == 200
    assert all(j["display_name"] == "paper2.pdf" for j in res2.json())
    job_ids_2 = {j["job_id"] for j in res2.json()}
    assert job_ids_2 == {j3["job_id"]}


def test_list_document_jobs_not_found_and_invalid_checksum(client: TestClient) -> None:
    res = client.get(f"/api/documents/{'0' * 64}/jobs")
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "document_not_found"

    res = client.get("/api/documents/invalid_checksum/jobs")
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "invalid_checksum"


def test_list_jobs_and_filtering(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    _, job_store, _, _ = stub_api_adapters

    client.post(
        "/api/documents",
        files={"file": ("paper.pdf", MINIMAL_PDF, "application/pdf")},
    )

    # Create 3 jobs: 2 mineru (1 queued, 1 parsed), 1 default queued
    res1 = client.post(f"/api/documents/{MINIMAL_PDF_HASH}/jobs", json={"parser": "mineru"})
    res2 = client.post(f"/api/documents/{MINIMAL_PDF_HASH}/jobs", json={"parser": "mineru"})
    res3 = client.post(f"/api/documents/{MINIMAL_PDF_HASH}/jobs", json={"parser": "default"})

    j1_id = res1.json()["job_id"]
    j2_id = res2.json()["job_id"]
    j3_id = res3.json()["job_id"]

    # Advance j2 to "parsed"
    parsed_state = job_store.states[j2_id].with_changes(
        status="parsed",
        parse_result_ref=f"pdf/{MINIMAL_PDF_HASH}/parsed/mineru/1",
    )
    job_store.states[j2_id] = parsed_state

    # 1. List all jobs
    all_res = client.get("/api/jobs", params={"detail": "full"})
    assert all_res.status_code == 200
    all_jobs = all_res.json()
    assert len(all_jobs) == 3
    jobs_by_id = {job["job_id"]: job for job in all_jobs}
    assert jobs_by_id[j1_id]["status"] == "queued"
    assert jobs_by_id[j2_id]["status"] == "parsed"
    assert jobs_by_id[j3_id]["status"] == "queued"
    assert all("has_parse_result" not in job for job in all_jobs)
    assert all("rows" not in job for job in all_jobs)
    assert all(job["document_checksum"] == MINIMAL_PDF_HASH for job in all_jobs)
    assert all(job["display_name"] == "paper.pdf" for job in all_jobs)
    assert all(datetime.fromisoformat(job["created_at"]).tzinfo is not None for job in all_jobs)

    # 2. Filter by status=queued
    queued_res = client.get("/api/jobs?status=queued")
    assert queued_res.status_code == 200
    queued_jobs = queued_res.json()
    assert len(queued_jobs) == 2
    assert {j["job_id"] for j in queued_jobs} == {j1_id, j3_id}

    # 3. Filter by parser=mineru
    mineru_res = client.get("/api/jobs?parser=mineru")
    assert mineru_res.status_code == 200
    mineru_jobs = mineru_res.json()
    assert len(mineru_jobs) == 2
    assert {j["job_id"] for j in mineru_jobs} == {j1_id, j2_id}

    # 4. Filter by both status=parsed and parser=mineru
    parsed_mineru_res = client.get("/api/jobs?status=parsed&parser=mineru")
    assert parsed_mineru_res.status_code == 200
    pm_jobs = parsed_mineru_res.json()
    assert len(pm_jobs) == 1
    assert pm_jobs[0]["job_id"] == j2_id
    assert pm_jobs[0]["status"] == "parsed"

    # 5. Invalid status filter returns 422
    invalid_status_res = client.get("/api/jobs?status=nonexistent")
    assert invalid_status_res.status_code == 422


def test_openapi_schema_includes_new_endpoints() -> None:
    schema = app.openapi()
    paths = schema["paths"]

    assert "/api/documents" in paths
    assert "post" in paths["/api/documents"]
    assert "get" in paths["/api/documents"]

    assert "/api/documents/{checksum}" in paths
    assert "get" in paths["/api/documents/{checksum}"]

    assert "/api/documents/{checksum}/file" in paths
    assert "get" in paths["/api/documents/{checksum}/file"]

    assert "/api/documents/{checksum}/jobs" in paths
    assert "post" in paths["/api/documents/{checksum}/jobs"]
    assert "get" in paths["/api/documents/{checksum}/jobs"]

    assert "/api/jobs" in paths
    assert "get" in paths["/api/jobs"]


def test_sanitize_display_name_behavior() -> None:
    from bioparser.api.uploads import (
        ascii_filename,
        inline_content_disposition,
        sanitize_display_name,
    )

    assert sanitize_display_name(None) is None
    assert sanitize_display_name("") is None
    assert sanitize_display_name("   ") is None
    assert sanitize_display_name('""\r\n\t') is None
    assert sanitize_display_name('paper"with"quotes.pdf') == "paperwithquotes.pdf"
    assert sanitize_display_name("line\nbreak.pdf") == "linebreak.pdf"
    assert sanitize_display_name("   simple.pdf   ") == "simple.pdf"
    long_name = "a" * 300 + ".pdf"
    assert len(sanitize_display_name(long_name) or "") == 255

    assert ascii_filename("käsikirja.pdf") == "kasikirja.pdf"
    assert ascii_filename("report.pdf") == "report.pdf"
    assert (
        inline_content_disposition("käsikirja.pdf")
        == "inline; filename=\"kasikirja.pdf\"; filename*=UTF-8''k%C3%A4sikirja.pdf"
    )
