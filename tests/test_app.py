from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx2
import pytest
from conftest import TEST_API_KEY
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx2 import ASGITransport
from pydantic import TypeAdapter, ValidationError
from stubs import StubArtifactStorage, StubJobs, StubParseQueue

from bioparser.api import config
from bioparser.api.app import app as api_app
from bioparser.api.errors import ErrorResponse
from bioparser.api.middleware import TypedRequestBodyLimitMiddleware
from bioparser.api.schema import (
    DoneJobFullResponse,
    DoneJobResponse,
    JobListResponse,
    JobStatusResponse,
)
from bioparser.api.uploads import max_body_bytes
from bioparser.db.engine import DatabaseError
from bioparser.ets.adapter import to_ets
from bioparser.ets.models import EtsRow
from bioparser.extract.models import (
    Citation,
    ExtractionArtifact,
    Interpretation,
    Observation,
    TextRange,
)
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

# A minimal byte string that passes the PDF upload checks.
MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
JOB_STATUS_ADAPTER: TypeAdapter[JobStatusResponse] = TypeAdapter(JobStatusResponse)
JOB_LIST_ADAPTER: TypeAdapter[list[JobListResponse]] = TypeAdapter(list[JobListResponse])
DOCUMENT_CHECKSUM = "a" * 64
PDF_REF = f"pdf/{DOCUMENT_CHECKSUM}"
PARSE_REF = f"{PDF_REF}/parsed/default/1"
EXTRACT_REF = f"{PARSE_REF}/extracted/default/1"
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
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
    values = {
        "job_id": uuid4(),
        "parser": "default",
        "extractor": "default",
        "status": status,
        "pdf_ref": PDF_REF,
        "created_at": CREATED_AT,
    }
    return JobState.model_validate(values | _STATUS_FIELDS[status] | fields)


def _job_json(state: JobState, **extra: Any) -> dict[str, Any]:
    """The default public body: the id and the status, plus anything that status adds."""
    return {"job_id": str(state.job_id), "status": state.status} | extra


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
    record = response.json()
    assert set(record) == {"job_id", "status"}
    assert record["status"] == "queued"
    assert events == [
        "artifact.store",
        "document.create",
        "job_state.create",
        "queue.submit",
    ]

    state = jobs.states[record["job_id"]]
    message = parse_queue.messages[0]
    assert message.schema_version == 1
    assert state.pdf_ref in artifact_storage.artifacts
    assert message.job_id == state.job_id

    response = client.get(f"/api/jobs/{record['job_id']}")

    assert response.status_code == 200
    JOB_STATUS_ADAPTER.validate_python(response.json())
    assert response.json() == record

    full = client.get(f"/api/jobs/{record['job_id']}?detail=full")
    assert full.status_code == 200
    assert full.json()["display_name"] == "sample.pdf"


def test_submit_uses_an_existing_document_display_name(
    client: TestClient,
) -> None:
    upload = client.post(
        "/api/documents",
        files={"file": ("paper.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert upload.status_code == 201

    submitted = client.post(
        "/api/extractions",
        files={"file": ("ignored.pdf", MINIMAL_PDF, "application/pdf")},
    )

    assert submitted.status_code == 202
    assert set(submitted.json()) == {"job_id", "status"}

    status = client.get(f"/api/jobs/{submitted.json()['job_id']}?detail=full")
    assert status.status_code == 200
    assert status.json()["display_name"] == "paper.pdf"


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
    over_limit = max_body_bytes() + 1
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


def test_job_status_openapi_schema() -> None:
    schema = api_app.openapi()
    response_schema = schema["paths"]["/api/jobs/{job_id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    done = schema["components"]["schemas"]["DoneJobResponse"]
    full = schema["components"]["schemas"]["DoneJobFullResponse"]

    assert response_schema["anyOf"] == [
        {"$ref": "#/components/schemas/OpenJobResponse"},
        {"$ref": "#/components/schemas/OpenJobFullResponse"},
        {"$ref": "#/components/schemas/DoneJobResponse"},
        {"$ref": "#/components/schemas/DoneJobFullResponse"},
        {"$ref": "#/components/schemas/FailedJobResponse"},
        {"$ref": "#/components/schemas/FailedJobFullResponse"},
    ]
    assert "drops" not in done["properties"]
    assert done["properties"]["rows"]["items"]["$ref"] == "#/components/schemas/EtsRow"
    assert full["properties"]["rows"]["items"]["$ref"] == "#/components/schemas/EtsSourceRow"
    assert "drops" not in full["properties"]
    assert "source_index" in schema["components"]["schemas"]["EtsSourceRow"]["properties"]
    assert done["properties"]["trait_list_version"]["type"] == "string"
    assert full["properties"]["trait_list_version"]["type"] == "string"
    assert "traitlist" not in done["properties"]
    assert "traitlist" not in full["properties"]
    assert "created_at" not in done["properties"]
    assert "document_checksum" not in done["properties"]
    assert "created_at" in full["properties"]
    assert "document_checksum" in full["properties"]
    assert "has_parse_result" not in full["properties"]
    assert "rows" not in schema["components"]["schemas"]["ListedJobResponse"]["properties"]
    assert "created_at" in schema["components"]["schemas"]["ListedJobFullResponse"]["properties"]


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
    state = _job("parsing")
    jobs.states[str(state.job_id)] = state

    response = client.get(f"/api/jobs/{state.job_id}")

    assert response.status_code == 200
    JOB_STATUS_ADAPTER.validate_python(response.json())
    assert response.json() == _job_json(state)


def test_succeeded_job_status(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    jobs = stub_api_adapters[1]
    state = _job("parsed")
    jobs.states[str(state.job_id)] = state

    response = client.get(f"/api/jobs/{state.job_id}")

    assert response.status_code == 200
    JOB_STATUS_ADAPTER.validate_python(response.json())
    assert response.json() == _job_json(state)


def _body_mass_artifact() -> ExtractionArtifact:
    cited = Citation(text="M. musculus", evidence=[TextRange(block_id="b0", start=0, end=11)])
    return ExtractionArtifact(
        extractor="default",
        extractor_version="1",
        observations=[
            Observation(
                traitCode=Interpretation(value="body_mass", passage=cited),
                verbatimTraitName=cited,
                verbatimScientificName=cited,
                verbatimTraitValue=Citation(
                    text="12.5", evidence=[TextRange(block_id="b0", start=20, end=24)]
                ),
                verbatimTraitUnit=Citation(
                    text="g", evidence=[TextRange(block_id="b0", start=25, end=26)]
                ),
            )
        ],
    )


def test_a_done_job_returns_its_ets_rows(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    artifact_storage, jobs, _queue, events = stub_api_adapters
    state = _job("done")
    jobs.states[str(state.job_id)] = state
    artifact = _body_mass_artifact()
    _store(artifact_storage, state.extract_result_ref or "", artifact.model_dump_json().encode())
    references = state.pdf_ref.rsplit("/", 1)[-1]
    expected = to_ets(artifact, references=references)

    response = client.get(f"/api/jobs/{state.job_id}")

    assert response.status_code == 200
    body = DoneJobResponse.model_validate(response.json())
    assert body.rows == [
        EtsRow.model_validate(row.model_dump(exclude={"source_index"})) for row in expected.rows
    ]
    assert response.json()["trait_list_version"] == "2026.10.0"
    assert response.json()["rows"][0]["verbatimTraitName"] == "M. musculus"
    assert response.json()["rows"][0]["traitName"] == "Body mass"
    assert response.json()["rows"][0]["traitID"] == "VT:0001259"
    assert response.json()["rows"][0]["traitValue"] == "12.5"
    assert response.json()["rows"][0]["traitUnit"] == "g"
    assert "source_index" not in response.json()["rows"][0]
    assert "drops" not in response.json()
    assert "artifact.retrieve" in events
    assert state.extract_result_ref is not None
    assert state.extract_result_ref not in response.text


def test_a_done_job_can_include_the_observation_index(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    artifact_storage, jobs, _queue, _events = stub_api_adapters
    state = _job("done")
    jobs.states[str(state.job_id)] = state
    first = _body_mass_artifact().observations[0]
    cited = first.verbatimScientificName
    second = first.model_copy(
        update={"traitCode": Interpretation(value="wing_length", passage=cited)}
    )
    artifact = _body_mass_artifact().model_copy(update={"observations": [first, second]})
    _store(artifact_storage, state.extract_result_ref or "", artifact.model_dump_json().encode())
    references = state.pdf_ref.rsplit("/", 1)[-1]
    expected = to_ets(artifact, references=references)

    response = client.get(f"/api/jobs/{state.job_id}?detail=full")

    assert response.status_code == 200
    body = DoneJobFullResponse.model_validate(response.json())
    assert body.rows == expected.rows
    assert [row.source_index for row in body.rows] == [0, 1]
    assert "drops" not in response.json()


def test_a_done_job_with_a_missing_artifact_is_a_safe_error(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    jobs = stub_api_adapters[1]
    state = _job("done")
    jobs.states[str(state.job_id)] = state

    response = client.get(f"/api/jobs/{state.job_id}")

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "artifact_invalid"


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (ArtifactNotFoundError("gone"), 500, "artifact_invalid"),
        (StorageError("disk"), 503, "storage_unavailable"),
    ],
    ids=["missing", "storage-down"],
)
def test_a_done_job_whose_artifact_cannot_be_read(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
    error: StorageError,
    status_code: int,
    code: str,
) -> None:
    artifact_storage, jobs, _queue, _events = stub_api_adapters
    state = _job("done")
    jobs.states[str(state.job_id)] = state
    artifact_storage.retrieve_error = error

    response = client.get(f"/api/jobs/{state.job_id}")

    assert response.status_code == status_code
    assert response.json()["detail"]["code"] == code
    assert "disk" not in response.text
    assert "gone" not in response.text


def test_a_done_job_with_an_invalid_artifact_is_a_safe_error(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    artifact_storage, jobs, _queue, _events = stub_api_adapters
    state = _job("done")
    jobs.states[str(state.job_id)] = state
    _store(artifact_storage, state.extract_result_ref or "", b"not-json")

    response = client.get(f"/api/jobs/{state.job_id}")

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "artifact_invalid"
    assert "not-json" not in response.text


def test_a_list_does_not_return_the_ets_rows_of_a_done_job(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    _storage, jobs, _queue, events = stub_api_adapters
    state = _job("done")
    jobs.states[str(state.job_id)] = state

    response = client.get("/api/jobs")

    assert response.status_code == 200
    body = response.json()
    JOB_LIST_ADAPTER.validate_python(body)
    assert body == [_job_json(state)]
    assert "artifact.retrieve" not in events


def test_job_status_does_not_read_the_artifact(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    """Re-reading the artifact on every poll ran its validation on the event loop."""
    artifact_storage, _jobs, _queue, events = stub_api_adapters
    artifact_storage.retrieve_error = StorageError("storage unavailable")
    state = _job("parsed")
    _jobs.states[str(state.job_id)] = state

    response = client.get(f"/api/jobs/{state.job_id}")

    assert response.status_code == 200
    assert response.json() == _job_json(state)
    assert "artifact.retrieve" not in events


@pytest.mark.parametrize("status", ["parsed"])
def test_a_finished_stage_names_no_storage_path(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
    status: JobStatus,
) -> None:
    jobs = stub_api_adapters[1]
    state = _job(status)
    jobs.states[str(state.job_id)] = state

    response = client.get(f"/api/jobs/{state.job_id}")

    assert response.json() == _job_json(state)


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
    state = _job("failed", error_code="parse_failed", parse_result_ref=None)
    jobs.states[str(state.job_id)] = state

    response = client.get(f"/api/jobs/{state.job_id}")

    assert response.status_code == 200
    JOB_STATUS_ADAPTER.validate_python(response.json())
    assert response.json() == _job_json(state, error={"code": "parse_failed"})


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


def test_job_status_response_requires_an_aware_creation_time() -> None:
    response = {
        "job_id": str(uuid4()),
        "status": "queued",
        "created_at": "2026-01-01T00:00:00",
        "display_name": None,
        "parser": "default",
        "extractor": "default",
        "document_checksum": DOCUMENT_CHECKSUM,
    }

    with pytest.raises(ValidationError, match="timezone"):
        JOB_STATUS_ADAPTER.validate_python(response)


def test_job_status_returns_503_when_display_name_read_fails(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    jobs = stub_api_adapters[1]
    state = _job("queued")
    jobs.states[str(state.job_id)] = state
    jobs.doc_get_error = DatabaseError("Postgres query failed: OperationalError")

    plain = client.get(f"/api/jobs/{state.job_id}")
    assert plain.status_code == 200

    response = client.get(f"/api/jobs/{state.job_id}?detail=full")

    assert response.status_code == 503
    ErrorResponse.model_validate(response.json())
    assert response.json()["detail"]["code"] == "database_unavailable"


@pytest.mark.parametrize("route", ["status", "list"])
def test_invalid_job_pdf_ref_returns_job_state_unreadable(
    client: TestClient,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
    route: str,
) -> None:
    jobs = stub_api_adapters[1]
    state = _job("queued", pdf_ref="legacy-ref")
    jobs.states[str(state.job_id)] = state
    path = f"/api/jobs/{state.job_id}" if route == "status" else "/api/jobs"

    assert client.get(path).status_code == 200
    response = client.get(path, params={"detail": "full"})

    assert response.status_code == 500
    ErrorResponse.model_validate(response.json())
    assert response.json()["detail"]["code"] == "job_state_unreadable"


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
    assert events == ["artifact.store", "document.create", "job_state.create"]
    assert jobs.documents == {}
    assert jobs.states == {}


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
        "document.create",
        "job_state.create",
        "queue.submit",
        "job_state.mark_failed",
    ]
    assert len(jobs.documents) == 1


def test_upload_limits(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config,
        "get_api_settings",
        lambda: config.ApiSettings(max_upload_bytes=1234),  # type: ignore[call-arg]
    )
    response = client.get("/api/upload-limits")
    assert response.status_code == 200
    assert response.json() == {"max_upload_bytes": 1234}
