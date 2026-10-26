from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx2
import pytest
from conftest import TEST_API_KEY
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx2 import ASGITransport
from pydantic import TypeAdapter
from stubs import StubArtifactStorage, StubJobs, StubParseQueue

from bioparser.api import config
from bioparser.api.app import app as api_app
from bioparser.api.errors import ErrorResponse
from bioparser.api.middleware import TypedRequestBodyLimitMiddleware
from bioparser.api.schema import JobStatusResponse
from bioparser.db.engine import DatabaseError
from bioparser.extract.models import ExtractionArtifact
from bioparser.jobs import (
    CorruptJobStateError,
    JobState,
    JobStatus,
)
from bioparser.jobs.queue import JobQueueError
from bioparser.storage import (
    ArtifactMetadata,
    ArtifactNotFoundError,
    FileSystemArtifactStorage,
    StorageError,
)

# A minimal byte string that passes every /api/extractions validation check.
MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
JOB_STATUS_ADAPTER: TypeAdapter[JobStatusResponse] = TypeAdapter(JobStatusResponse)
PARSE_REF = f"pdf/{'a' * 64}/parsed/default/1"
EXTRACT_REF = f"{PARSE_REF}/extracted/default/1"
# What each status needs on a stored job besides its id: refs and error
_STATUS_FIELDS: dict[JobStatus, dict[str, Any]] = {
    "queued": {},
    "parsing": {},
    "parsed": {"parse_result_ref": PARSE_REF},
    "extracting": {"parse_result_ref": PARSE_REF},
    "done": {"parse_result_ref": PARSE_REF, "extract_result_ref": EXTRACT_REF},
    "failed": {"parse_result_ref": PARSE_REF, "error_code": "extraction_failed"},
}


def _job(status: JobStatus, **fields: Any) -> JobState:
    """A valid stored job in [status]. [fields] replace the defaults for that status."""
    return JobState(
        job_id=uuid4(),
        parser="default",
        extractor="default",
        status=status,
        pdf_ref=f"pdf/{'a' * 64}",
        **(_STATUS_FIELDS[status] | fields),
    )


def test_submit_and_retrieve_job(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    artifact_storage, jobs, parse_queue, events = stub_api_adapters
    response = client.post(
        "/api/extractions",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 202
    JOB_STATUS_ADAPTER.validate_python(response.json())
    record = response.json()

    assert record["job_id"]
    assert record["status"] == "queued"
    assert events == ["artifact.store", "job_state.create", "queue.submit"]

    state = jobs.states[record["job_id"]]
    message = parse_queue.messages[0]
    assert message.schema_version == 1
    assert state.pdf_ref in artifact_storage.artifacts
    assert message.job_id == state.job_id

    response = client.get(f"/api/jobs/{record['job_id']}")

    assert response.status_code == 200
    JOB_STATUS_ADAPTER.validate_python(response.json())
    assert response.json() == record


def test_job_status_openapi_schema() -> None:
    schema = api_app.openapi()
    response_schema = schema["paths"]["/api/jobs/{job_id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]

    assert response_schema["discriminator"] == {
        "propertyName": "status",
        "mapping": {
            "queued": "#/components/schemas/QueuedJobResponse",
            "parsing": "#/components/schemas/RunningJobResponse",
            "extracting": "#/components/schemas/RunningJobResponse",
            "parsed": "#/components/schemas/SucceededJobResponse",
            "done": "#/components/schemas/SucceededJobResponse",
            "failed": "#/components/schemas/FailedJobResponse",
        },
    }
    assert response_schema["oneOf"] == [
        {"$ref": "#/components/schemas/QueuedJobResponse"},
        {"$ref": "#/components/schemas/RunningJobResponse"},
        {"$ref": "#/components/schemas/SucceededJobResponse"},
        {"$ref": "#/components/schemas/FailedJobResponse"},
    ]


def test_unknown_job_returns_404(client: TestClient) -> None:
    response = client.get(f"/api/jobs/{uuid4()}")

    assert response.status_code == 404
    ErrorResponse.model_validate(response.json())
    assert response.json() == {"detail": {"code": "job_not_found", "message": "Job not found"}}


@pytest.mark.parametrize(
    "job_id",
    [
        "missing",
        "11111111-1111-1111-1111-11111111111",  # one character short
        "00000000-0000-0000-0000-000000000000",  # nil UUID, not v4
        str(UUID(int=1, version=1)),  # valid UUID, wrong version
    ],
)
def test_malformed_job_id_returns_typed_422(client: TestClient, job_id: str) -> None:
    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 422
    ErrorResponse.model_validate(response.json())
    assert response.json() == {
        "detail": {"code": "invalid_job_id", "message": "job_id must be a version 4 UUID"}
    }


def test_submit_pdf(client: TestClient) -> None:
    response = client.post(
        "/api/extractions",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


@pytest.mark.anyio
async def test_duplicate_pdf_submission_reuses_stored_artifact(
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, jobs, parse_queue, _ = stub_api_adapters
    artifact_storage = FileSystemArtifactStorage(tmp_path / "artifacts")
    monkeypatch.setattr(api_app.state, "artifact_storage", artifact_storage)

    async with httpx2.AsyncClient(
        transport=ASGITransport(api_app),
        base_url="http://test",
        headers={"X-API-Key": TEST_API_KEY},
    ) as client:
        responses = [
            await client.post(
                "/api/extractions",
                files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
            )
            for _ in range(2)
        ]

    assert [response.status_code for response in responses] == [202, 202]
    job_ids = [response.json()["job_id"] for response in responses]
    assert job_ids[0] != job_ids[1]
    pdf_refs = {jobs.states[job_id].pdf_ref for job_id in job_ids}
    assert len(pdf_refs) == 1
    pdf_ref = pdf_refs.pop()
    assert await artifact_storage.retrieve(pdf_ref) == MINIMAL_PDF
    assert [str(message.job_id) for message in parse_queue.messages] == job_ids


def test_submit_missing_file_part(client: TestClient) -> None:
    response = client.post("/api/extractions")

    assert response.status_code == 400
    ErrorResponse.model_validate(response.json())
    detail = response.json()["detail"]
    assert detail["code"] == "missing_file"
    assert detail["message"]


def test_submit_unsupported_content_type(client: TestClient) -> None:
    response = client.post(
        "/api/extractions",
        files={"file": ("notes.txt", b"just some text", "text/plain")},
    )

    assert response.status_code == 415
    detail = response.json()["detail"]
    assert detail["code"] == "unsupported_content_type"
    assert detail["message"]


def test_submit_content_type_with_parameters(client: TestClient) -> None:
    response = client.post(
        "/api/extractions",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf;charset=binary")},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_submit_content_type_case_insensitive(client: TestClient) -> None:
    response = client.post(
        "/api/extractions",
        files={"file": ("sample.pdf", MINIMAL_PDF, "APPLICATION/PDF")},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_submit_empty_file(client: TestClient) -> None:
    response = client.post(
        "/api/extractions",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "empty_file"
    assert detail["message"]


def test_submit_non_pdf_bytes(client: TestClient) -> None:
    response = client.post(
        "/api/extractions",
        files={"file": ("sample.pdf", b"not a pdf at all", "application/pdf")},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_pdf"
    assert detail["message"]


def test_submit_file_too_large(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config,
        "get_api_settings",
        lambda: config.ApiSettings(max_upload_bytes=10),  # type: ignore[call-arg]
    )

    response = client.post(
        "/api/extractions",
        files={"file": ("big.pdf", MINIMAL_PDF, "application/pdf")},
    )

    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/json")
    ErrorResponse.model_validate(response.json())
    assert response.json() == {
        "detail": {
            "code": "content_too_large",
            "message": "Content Too Large",
        }
    }


def test_submit_declared_content_length_too_large(client: TestClient) -> None:
    over_limit = config.DEFAULT_MAX_UPLOAD_BYTES + 1
    response = client.post(
        "/api/extractions",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
        headers={"content-length": str(over_limit)},
    )

    assert response.status_code == 413
    ErrorResponse.model_validate(response.json())
    assert response.json() == {
        "detail": {
            "code": "content_too_large",
            "message": "Content Too Large",
        }
    }


def test_submit_invalid_content_length(client: TestClient) -> None:
    response = client.post(
        "/api/extractions",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
        headers={"content-length": "abc"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_content_length"
    assert detail["message"]


def test_submit_negative_content_length(client: TestClient) -> None:
    response = client.post(
        "/api/extractions",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
        headers={"content-length": "-1"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_content_length"
    assert detail["message"]


def test_streamed_body_too_large_with_understated_content_length() -> None:
    app = FastAPI()
    app.add_middleware(TypedRequestBodyLimitMiddleware, max_body_size=5)

    @app.post("/")
    async def endpoint(request: Request) -> dict[str, bool]:
        await request.body()
        return {"ok": True}

    client = TestClient(app)

    response = client.post(
        "/",
        content=b"123456789",
        headers={"content-length": "1"},
    )

    assert response.status_code == 413
    ErrorResponse.model_validate(response.json())
    assert response.json() == {
        "detail": {
            "code": "content_too_large",
            "message": "Content Too Large",
        }
    }


def test_running_job_status(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    jobs = stub_api_adapters[1]
    job_id = uuid4()

    state = JobState(
        job_id=job_id,
        parser="default",
        extractor="default",
        status="parsing",
        pdf_ref="art-pdf-test",
    )
    jobs.states[str(job_id)] = state

    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    JOB_STATUS_ADAPTER.validate_python(response.json())
    assert response.json() == {"job_id": str(job_id), "status": "parsing"}


def test_succeeded_job_status(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    jobs = stub_api_adapters[1]
    job_id = uuid4()
    jobs.states[str(job_id)] = JobState(
        job_id=job_id,
        parser="default",
        extractor="default",
        status="parsed",
        pdf_ref="pdf/test",
        parse_result_ref="art-result-test",
    )

    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    JOB_STATUS_ADAPTER.validate_python(response.json())
    assert response.json() == {"job_id": str(job_id), "status": "parsed"}


def test_job_status_does_not_read_the_artifact(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    """Re-reading the artifact on every poll ran its validation on the event loop."""
    artifact_storage, jobs, _queue, events = stub_api_adapters
    artifact_storage.retrieve_error = StorageError("storage unavailable")
    job_id = uuid4()
    jobs.states[str(job_id)] = JobState(
        job_id=job_id,
        parser="default",
        extractor="default",
        status="parsed",
        pdf_ref="pdf/test",
        parse_result_ref="art-result-test",
    )

    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    assert "artifact.retrieve" not in events


@pytest.mark.parametrize("status", ["parsed", "done"])
def test_a_finished_stage_names_no_storage_path(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
    status: JobStatus,
) -> None:
    jobs = stub_api_adapters[1]
    state = _job(status)
    jobs.states[str(state.job_id)] = state

    response = client.get(f"/api/jobs/{state.job_id}")

    assert response.json() == {"job_id": str(state.job_id), "status": status}


# --- job artifacts for the UI -------------------------------------------------

EXTRACTION = ExtractionArtifact(extractor="default", extractor_version="1")
# (route, stored ref, statuses that have it)
ARTIFACT_ROUTES = [
    ("parser", PARSE_REF, ["parsed", "extracting", "done", "failed"]),
    ("extraction", EXTRACT_REF, ["done"]),
]
ALL_STATUSES: list[JobStatus] = ["queued", "parsing", "parsed", "extracting", "done", "failed"]


def _store(storage: StubArtifactStorage, ref: str, payload: bytes) -> None:
    storage.artifacts[ref] = (
        payload,
        ArtifactMetadata(artifact_path=ref, media_type="application/json"),
    )


@pytest.mark.parametrize(
    ("kind", "ref", "status"),
    [(kind, ref, status) for kind, ref, statuses in ARTIFACT_ROUTES for status in statuses],
)
def test_a_job_returns_its_artifact_as_stored(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
    kind: str,
    ref: str,
    status: JobStatus,
) -> None:
    artifact_storage, jobs = stub_api_adapters[:2]
    state = _job(status)
    jobs.states[str(state.job_id)] = state
    stored = EXTRACTION.model_dump_json().encode() + b"\n"  # bytes, not a re-serialization
    _store(artifact_storage, ref, stored)

    response = client.get(f"/api/jobs/{state.job_id}/artifacts/{kind}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert response.content == stored


@pytest.mark.parametrize(
    ("kind", "status"),
    [
        (kind, status)
        for kind, _ref, statuses in ARTIFACT_ROUTES
        for status in ALL_STATUSES
        if status not in statuses
    ],
)
def test_a_job_without_the_artifact_gets_409(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
    kind: str,
    status: JobStatus,
) -> None:
    _storage, jobs, _queue, events = stub_api_adapters
    state = _job(status)
    jobs.states[str(state.job_id)] = state

    response = client.get(f"/api/jobs/{state.job_id}/artifacts/{kind}")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "artifact_not_available"
    assert "cache-control" not in response.headers
    assert "artifact.retrieve" not in events


def test_a_job_that_failed_before_parsing_has_no_parser_artifact(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    jobs = stub_api_adapters[1]
    state = _job("failed", parse_result_ref=None, error_code="invalid_pdf")
    jobs.states[str(state.job_id)] = state

    response = client.get(f"/api/jobs/{state.job_id}/artifacts/parser")

    assert response.status_code == 409


@pytest.mark.parametrize("kind", ["parser", "extraction"])
def test_the_artifact_of_an_unknown_job_is_404(client: TestClient, kind: str) -> None:
    response = client.get(f"/api/jobs/{uuid4()}/artifacts/{kind}")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "job_not_found"


@pytest.mark.parametrize("kind", ["parser", "extraction"])
def test_an_artifact_route_needs_a_version_4_job_id(client: TestClient, kind: str) -> None:
    response = client.get(f"/api/jobs/{UUID(int=1)}/artifacts/{kind}")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_job_id"


@pytest.mark.parametrize(
    ("error", "status_code", "code", "message"),
    [
        (ArtifactNotFoundError("gone"), 500, "artifact_invalid", "job artifact missing or invalid"),
        (StorageError("disk"), 503, "storage_unavailable", "job artifact read failed"),
    ],
    ids=["missing", "storage-down"],
)
def test_an_artifact_that_cannot_be_read(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
    caplog: pytest.LogCaptureFixture,
    error: StorageError,
    status_code: int,
    code: str,
    message: str,
) -> None:
    artifact_storage, jobs = stub_api_adapters[:2]
    state = _job("done")
    jobs.states[str(state.job_id)] = state
    artifact_storage.retrieve_error = error

    response = client.get(f"/api/jobs/{state.job_id}/artifacts/extraction")

    assert response.status_code == status_code
    assert response.json()["detail"]["code"] == code
    [record] = [r for r in caplog.records if r.getMessage() == message]
    assert (vars(record)["job_id"], vars(record)["artifact"]) == (str(state.job_id), "extraction")
    assert EXTRACT_REF not in caplog.text  # the log names the artifact, not its storage path


@pytest.mark.parametrize(
    ("kind", "schema"), [("parser", "ParserArtifact"), ("extraction", "ExtractionArtifact")]
)
def test_the_artifact_routes_are_documented_as_internal(kind: str, schema: str) -> None:
    operation = api_app.openapi()["paths"][f"/api/jobs/{{job_id}}/artifacts/{kind}"]["get"]

    assert operation["tags"] == ["internal"]
    assert "can change" in operation["description"]
    body = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert body == {"$ref": f"#/components/schemas/{schema}"}


def test_failed_job_status(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    jobs = stub_api_adapters[1]
    job_id = uuid4()

    state = JobState(
        job_id=job_id,
        parser="default",
        extractor="default",
        status="failed",
        pdf_ref="art-pdf-test",
        error_code="parse_failed",
    )

    jobs.states[str(job_id)] = state

    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    JOB_STATUS_ADAPTER.validate_python(response.json())
    assert response.json() == {
        "job_id": str(job_id),
        "status": "failed",
        "error": {"code": "parse_failed"},
    }


def test_job_status_returns_503_when_postgres_unavailable(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    jobs = stub_api_adapters[1]
    jobs.get_error = DatabaseError("Postgres query failed: OperationalError")

    response = client.get(f"/api/jobs/{uuid4()}")

    assert response.status_code == 503
    ErrorResponse.model_validate(response.json())
    assert response.json()["detail"]["code"] == "database_unavailable"


def test_job_status_returns_500_when_stored_state_is_unreadable(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    jobs = stub_api_adapters[1]
    jobs.get_error = CorruptJobStateError("invalid stored state")

    response = client.get(f"/api/jobs/{uuid4()}")

    assert response.status_code == 500
    ErrorResponse.model_validate(response.json())
    assert response.json() == {
        "detail": {
            "code": "job_state_unreadable",
            "message": "Stored job state is unreadable",
        }
    }


def test_submit_returns_503_when_postgres_unavailable(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    artifact_storage, jobs, parse_queue, events = stub_api_adapters
    jobs.create_error = DatabaseError("Postgres query failed: OperationalError")

    response = client.post(
        "/api/extractions",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
    )

    assert response.status_code == 503
    ErrorResponse.model_validate(response.json())
    assert response.json()["detail"]["code"] == "database_unavailable"
    assert len(artifact_storage.artifacts) == 1
    assert parse_queue.messages == []
    assert events == ["artifact.store", "job_state.create"]


def test_submit_returns_503_when_storage_unavailable(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    artifact_storage, jobs, parse_queue, events = stub_api_adapters
    artifact_storage.store_error = StorageError("Storage unavailable")

    response = client.post(
        "/api/extractions",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
    )

    assert response.status_code == 503
    ErrorResponse.model_validate(response.json())
    assert response.json()["detail"]["code"] == "storage_unavailable"
    assert artifact_storage.artifacts == {}
    assert jobs.states == {}
    assert parse_queue.messages == []
    assert events == ["artifact.store"]


def test_submit_returns_503_when_queue_unavailable(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    artifact_storage, jobs, parse_queue, events = stub_api_adapters
    parse_queue.submit_error = JobQueueError("Queue unavailable")
    response = client.post(
        "/api/extractions",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 503
    ErrorResponse.model_validate(response.json())
    assert response.json()["detail"]["code"] == "queue_unavailable"
    assert len(artifact_storage.artifacts) == 1
    assert parse_queue.messages == []
    assert len(jobs.states) == 1
    failed_state = next(iter(jobs.states.values()))
    assert failed_state.status == "failed"
    assert failed_state.error_code == "internal_error"
    assert events == [
        "artifact.store",
        "job_state.create",
        "queue.submit",
        "job_state.mark_failed",
    ]
