import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from stubs import StubArtifactStorage, StubJobStateStore, StubParseQueue

from bioparser.api import config
from bioparser.api.app import app as api_app
from bioparser.api.errors import ErrorResponse
from bioparser.api.middleware import TypedRequestBodyLimitMiddleware
from bioparser.api.schema import JobStatusResponse
from bioparser.jobqueue import PARSE_JOB_SCHEMA_VERSION, JobQueueError
from bioparser.jobstate import JobState, JobStateError, SafeError
from bioparser.parser import ParserArtifact
from bioparser.parser.models import ParserInfo
from bioparser.storage import (
    ArtifactMetadata,
    ArtifactNotFoundError,
    CreationInfo,
    FileSystemArtifactStorage,
    StorageError,
)

# A minimal byte string that passes every /api/extractions validation check.
MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
JOB_STATUS_ADAPTER: TypeAdapter[JobStatusResponse] = TypeAdapter(JobStatusResponse)


def test_submit_and_retrieve_job(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]],
) -> None:
    artifact_storage, job_store, parse_queue, events = stub_api_adapters
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

    state = job_store.states[record["job_id"]]
    message = parse_queue.messages[0]
    assert message.schema_version == PARSE_JOB_SCHEMA_VERSION
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
    monkeypatch.setattr(config, "get_api_settings", lambda: config.ApiSettings(max_upload_bytes=10))

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
    stub_api_adapters: tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]],
) -> None:
    job_store = stub_api_adapters[1]
    job_id = uuid4()

    state = JobState(
        job_id=job_id,
        parser="default",
        status="parsing",
        pdf_ref="art-pdf-test",
    )
    job_store.states[str(job_id)] = state

    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    JOB_STATUS_ADAPTER.validate_python(response.json())
    assert response.json() == {"job_id": job_id, "status": "parsing"}


def test_succeeded_job_status(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]],
) -> None:
    artifact_storage, job_store = stub_api_adapters[:2]
    job_id = uuid4()

    state = JobState(
        job_id=job_id,
        parser="default",
        status="parsed",
        pdf_ref="pdf/test",
        parse_result_ref="art-result-test",
    )

    job_store.states[str(job_id)] = state
    artifact_storage.artifacts["art-result-test"] = (
        json.dumps(
            ParserArtifact(
                schema_version="1",
                checksum="a" * 64,
                parser=ParserInfo(name="test", version="1", configuration={}),
                pages=[],
            ).model_dump()
        ).encode(),
        ArtifactMetadata(
            artifact_path="art-result-test",
            media_type="application/json",
        ),
    )

    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    JOB_STATUS_ADAPTER.validate_python(response.json())
    assert response.json() == {
        "job_id": str(job_id),
        "status": "parsed",
        "output_artifact_ref": "art-result-test",
    }


def test_succeeded_job_status_returns_503_when_output_is_missing(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]],
) -> None:
    artifact_storage, job_store = stub_api_adapters[:2]
    job_id = uuid4()
    job_store.states[str(job_id)] = JobState(
        job_id=job_id,
        parser="default",
        status="parsed",
        pdf_ref="art-pdf-test",
        parse_result_ref="art-result-test",
    )
    artifact_storage.retrieve_error = ArtifactNotFoundError("missing")

    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "storage_unavailable"


def test_succeeded_job_status_returns_503_for_invalid_output(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]],
) -> None:
    artifact_storage, job_store = stub_api_adapters[:2]
    job_id = uuid4()
    job_store.states[str(job_id)] = JobState(
        job_id=job_id,
        parser="default",
        status="parsed",
        pdf_ref="art-pdf-test",
        parse_result_ref="art-result-test",
    )
    artifact_storage.artifacts["art-result-test"] = (
        b"{}",
        ArtifactMetadata(
            artifact_path="art-result-test",
            media_type="application/json",
        ),
    )
    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "artifact_invalid"


def test_succeeded_job_status_reads_parser_artifact_from_filesystem(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, job_store = stub_api_adapters[:2]
    artifact_storage = FileSystemArtifactStorage(tmp_path / "artifacts")
    monkeypatch.setattr(api_app.state, "artifact_storage", artifact_storage)
    job_id = uuid4()
    output_path = f"pdf/{'a' * 64}/parsed/test/1"
    artifact = ParserArtifact(
        schema_version="1",
        checksum="a" * 64,
        parser=ParserInfo(name="test", version="1", configuration={}),
        pages=[],
    )
    artifact_storage.store(
        artifact.model_dump_json().encode(),
        ArtifactMetadata(
            artifact_path=output_path,
            media_type="application/json",
            creation_info=CreationInfo(created_by="test"),
        ),
    )
    job_store.states[str(job_id)] = JobState(
        job_id=job_id,
        parser="default",
        status="parsed",
        pdf_ref="pdf/input",
        parse_result_ref=output_path,
    )

    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json() == {
        "job_id": str(job_id),
        "status": "parsed",
        "output_artifact_ref": output_path,
    }


def test_failed_job_status(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]],
) -> None:
    job_store = stub_api_adapters[1]
    job_id = uuid4()

    state = JobState(
        job_id=job_id,
        parser="default",
        status="failed",
        pdf_ref="art-pdf-test",
        error=SafeError(
            code="parse_failed",
        ),
    )

    job_store.states[str(job_id)] = state

    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    JOB_STATUS_ADAPTER.validate_python(response.json())
    assert response.json() == {
        "job_id": job_id,
        "status": "failed",
        "error": {"code": "parse_failed"},
    }


def test_job_status_returns_503_when_redis_unavailable(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]],
) -> None:
    job_store = stub_api_adapters[1]
    job_store.get_error = JobStateError("Redis unavailable")

    response = client.get(f"/api/jobs/{uuid4()}")

    assert response.status_code == 503
    ErrorResponse.model_validate(response.json())
    assert response.json()["detail"]["code"] == "redis_unavailable"


def test_submit_returns_503_when_redis_unavailable(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]],
) -> None:
    artifact_storage, job_store, parse_queue, events = stub_api_adapters
    job_store.create_error = JobStateError("Redis unavailable")

    response = client.post(
        "/api/extractions",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
    )

    assert response.status_code == 503
    ErrorResponse.model_validate(response.json())
    assert response.json()["detail"]["code"] == "redis_unavailable"
    assert len(artifact_storage.artifacts) == 1
    assert parse_queue.messages == []
    assert events == ["artifact.store", "job_state.create"]


def test_submit_returns_503_when_storage_unavailable(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]],
) -> None:
    artifact_storage, job_store, parse_queue, events = stub_api_adapters
    artifact_storage.store_error = StorageError("Storage unavailable")

    response = client.post(
        "/api/extractions",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
    )

    assert response.status_code == 503
    ErrorResponse.model_validate(response.json())
    assert response.json()["detail"]["code"] == "storage_unavailable"
    assert artifact_storage.artifacts == {}
    assert job_store.states == {}
    assert parse_queue.messages == []
    assert events == ["artifact.store"]


def test_submit_returns_503_when_queue_unavailable(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]],
) -> None:
    artifact_storage, job_store, parse_queue, events = stub_api_adapters
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
    assert len(job_store.states) == 2
    failed_state = next(state for state in job_store.states.values() if state.status == "failed")
    assert failed_state.status == "failed"
    assert failed_state.error == SafeError(code="internal_error")
    assert events == [
        "artifact.store",
        "job_state.create",
        "queue.submit",
        "job_state.update",
    ]
