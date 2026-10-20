import logging
from collections.abc import Awaitable, Callable
from typing import cast
from uuid import UUID

import pytest
from stubs import StubVLLMService

from bioparser.artifact_paths import extract_result_path
from bioparser.extract.extract import ExtractionFailedError, ExtractionReport, extract_observations
from bioparser.extract.schema import ExtractionArtifact
from bioparser.jobqueue import (
    ExtractJobMessage,
    JobDeferralExpired,
    JobDeferred,
    JobTimeLimitExceeded,
)
from bioparser.jobstate import JobNotFoundError, JobState, SafeError, StaleJobStateWriteError
from bioparser.jobstate.models import ALLOWED_TRANSITIONS
from bioparser.parser.models import (
    PARSER_ARTIFACT_SCHEMA_VERSION,
    BlockKind,
    BlockRole,
    ContentBlock,
    Page,
    ParserArtifact,
    ParserInfo,
    TextContent,
)
from bioparser.services.vllm import VLLMService
from bioparser.services.vllm.vllm import VLLMError, VLLMRequestRejectedError, VLLMTruncatedError
from bioparser.storage import StoredArtifact
from bioparser.storage.errors import ArtifactAlreadyExistsError, ArtifactNotFoundError
from bioparser.storage.models import ArtifactMetadata
from bioparser.worker.extract.config import DEFAULT_EXTRACT_MAX_RETRIES
from bioparser.worker.extract.handler import ExtractJobHandler

JOB_ID = UUID("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e34")
PARSE_REF = "6b1f0c2a-7d44-4e1a-9c3b-2f8e5a0d6c11"
PDF_CHECKSUM = "abc"
RESULT_PATH = extract_result_path(PDF_CHECKSUM, "default", "1", "default", "0")
QUOTE = "Adult body mass averaged 12.5 g in the sampled population."


class MemoryJobs:
    def __init__(self, state: JobState | None) -> None:
        self.state = state

    async def create(self, state: JobState) -> None:
        self.state = state

    async def get(self, job_id: UUID) -> JobState | None:
        if self.state is None or self.state.job_id != job_id:
            return None
        return self.state

    async def update(self, state: JobState) -> None:
        if self.state is None or self.state.job_id != state.job_id:
            raise JobNotFoundError(str(state.job_id))
        if state.status not in ALLOWED_TRANSITIONS[self.state.status]:
            raise StaleJobStateWriteError(state.status)
        self.state = state

    async def aclose(self) -> None:
        return


class MemoryStorage:
    def __init__(self) -> None:
        self.payloads: dict[str, bytes] = {PARSE_REF: _parser_artifact().model_dump_json().encode()}
        self.metadata: dict[str, ArtifactMetadata] = {}
        self.stores = 0

    def store(self, content: bytes, metadata: ArtifactMetadata) -> str:
        if metadata.artifact_path in self.payloads:
            raise ArtifactAlreadyExistsError(metadata.artifact_path)
        self.payloads[metadata.artifact_path] = content
        self.metadata[metadata.artifact_path] = metadata
        self.stores += 1
        return metadata.artifact_path

    def retrieve(self, artifact_path: str) -> bytes:
        try:
            return self.payloads[artifact_path]
        except KeyError as exc:
            raise ArtifactNotFoundError(artifact_path) from exc

    def exists(self, artifact_path: str) -> bool:
        return artifact_path in self.payloads

    def retrieve_metadata(self, artifact_path: str) -> ArtifactMetadata:
        return self.metadata[artifact_path]

    def retrieve_artifact(self, artifact_path: str) -> StoredArtifact:
        raise NotImplementedError


def _parser_artifact(text: str = QUOTE) -> ParserArtifact:
    return ParserArtifact(
        schema_version=PARSER_ARTIFACT_SCHEMA_VERSION,
        checksum=PDF_CHECKSUM,
        parser=ParserInfo(name="default", version="1", configuration={}),
        pages=[
            Page(
                page_number=1,
                width=100.0,
                height=100.0,
                blocks=[
                    ContentBlock(
                        block_id="b0",
                        order=0,
                        kind=BlockKind.TEXT,
                        role=BlockRole.BODY,
                        content=TextContent(text=text),
                    )
                ],
            )
        ],
    )


def _parsed() -> JobState:
    return JobState(
        job_id=JOB_ID,
        status="parsed",
        parser="default",
        pdf_ref="pdf/abc",
        parse_result_ref=PARSE_REF,
    )


def _message() -> ExtractJobMessage:
    return ExtractJobMessage(job_id=JOB_ID)


def _real_extract(vllm: StubVLLMService) -> Callable[[ParserArtifact], Awaitable[ExtractionReport]]:
    service = cast(VLLMService, vllm)

    def extract(artifact: ParserArtifact) -> Awaitable[ExtractionReport]:
        return extract_observations(service, artifact, char_budget=8000, max_tokens=256)

    return extract


def _handler(
    jobs: MemoryJobs,
    storage: MemoryStorage,
    extract: Callable[[ParserArtifact], Awaitable[ExtractionReport]],
    *,
    max_claims: int = DEFAULT_EXTRACT_MAX_RETRIES + 1,
) -> ExtractJobHandler:
    return ExtractJobHandler(
        jobs=jobs,
        storage=storage,
        extract=extract,
        max_claims=max_claims,
    )


def test_success_stores_the_report_and_marks_done() -> None:
    jobs = MemoryJobs(_parsed())
    storage = MemoryStorage()
    vllm = StubVLLMService()
    handler = _handler(jobs, storage, _real_extract(vllm))
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.status == "done"
    assert jobs.state.extract_result_ref == RESULT_PATH
    assert jobs.state.parse_result_ref == PARSE_REF
    assert jobs.state.claims("extracting") == 1
    stored = ExtractionArtifact.model_validate_json(storage.retrieve(RESULT_PATH))
    assert stored.observations[0].grounded is True
    assert stored.observations[0].quotation == "12.5 g"
    assert storage.metadata[RESULT_PATH].created_by == "extractor-worker"
    assert vllm.prompts


def test_duplicate_delivery_does_not_call_the_model_again() -> None:
    jobs = MemoryJobs(_parsed())
    storage = MemoryStorage()
    vllm = StubVLLMService()
    handler = _handler(jobs, storage, _real_extract(vllm))
    message = _message()
    handler.handle(message)
    prompts = len(vllm.prompts)
    handler.handle(message)
    handler.close()

    assert len(vllm.prompts) == prompts
    assert storage.stores == 1
    assert jobs.state is not None
    assert jobs.state.status == "done"


def test_an_ungrounded_observation_is_stored_flagged() -> None:
    jobs = MemoryJobs(_parsed())
    storage = MemoryStorage()
    vllm = StubVLLMService(
        observations=[
            {
                "taxon": "Mus musculus",
                "trait": "body_mass",
                "value": 12.5,
                "unit": "g",
                "evidence_block_id": "b0",
                "quotation": "not in the paper",
            }
        ]
    )
    handler = _handler(jobs, storage, _real_extract(vllm))
    handler.handle(_message())
    handler.close()

    stored = ExtractionArtifact.model_validate_json(storage.retrieve(RESULT_PATH))
    assert stored.ungrounded == 1
    assert stored.observations[0].grounded is False
    assert jobs.state is not None
    assert jobs.state.status == "done"


def test_schema_failure_is_permanent() -> None:
    jobs = MemoryJobs(_parsed())

    async def extract(artifact: ParserArtifact) -> ExtractionReport:
        raise ExtractionFailedError("schema")

    handler = _handler(jobs, MemoryStorage(), extract)
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="extraction_failed")
    assert jobs.state.parse_result_ref == PARSE_REF


def test_truncated_output_is_permanent() -> None:
    jobs = MemoryJobs(_parsed())

    async def extract(artifact: ParserArtifact) -> ExtractionReport:
        raise VLLMTruncatedError("cut off")

    handler = _handler(jobs, MemoryStorage(), extract)
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.error == SafeError(code="extraction_failed")


def test_a_rejected_request_is_permanent() -> None:
    jobs = MemoryJobs(_parsed())

    async def extract(artifact: ParserArtifact) -> ExtractionReport:
        raise VLLMRequestRejectedError("vLLM rejected the request: BadRequestError")

    handler = _handler(jobs, MemoryStorage(), extract)
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="extraction_failed")
    assert jobs.state.claims("extracting") == 1


def test_vllm_down_leaves_the_job_parsed(caplog: pytest.LogCaptureFixture) -> None:
    jobs = MemoryJobs(_parsed())
    vllm = StubVLLMService()
    vllm.up = False
    called = False

    async def extract(artifact: ParserArtifact) -> ExtractionReport:
        nonlocal called
        called = True
        raise AssertionError("model was called")

    handler = ExtractJobHandler(
        jobs=jobs,
        storage=MemoryStorage(),
        extract=extract,
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
        vllm=cast(VLLMService, vllm),
    )
    with (
        caplog.at_level(logging.WARNING, logger="bioparser.worker.extract.handler"),
        pytest.raises(JobDeferred),
    ):
        handler.handle(_message())
    handler.close()

    assert called is False
    assert "vLLM is not answering" in caplog.text
    assert jobs.state is not None
    assert jobs.state.status == "parsed"
    assert jobs.state.claims("extracting") == 0


def test_vllm_answering_still_extracts() -> None:
    jobs = MemoryJobs(_parsed())
    storage = MemoryStorage()
    vllm = StubVLLMService()
    handler = ExtractJobHandler(
        jobs=jobs,
        storage=storage,
        extract=_real_extract(vllm),
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
        vllm=cast(VLLMService, vllm),
    )
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.status == "done"
    assert vllm.prompts


def test_on_failed_on_a_parsed_job_says_it_cannot_be_marked_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    jobs = MemoryJobs(_parsed())
    handler = _handler(jobs, MemoryStorage(), _real_extract(StubVLLMService()))
    with caplog.at_level(logging.INFO, logger="bioparser.worker.stage"):
        handler.on_failed(_message(), RuntimeError("wake-up failed"))
    handler.close()

    assert jobs.state is not None
    assert jobs.state.status == "parsed"
    assert "cannot be marked failed" in caplog.text
    assert "already finished or moved on" not in caplog.text


def test_vllm_errors_are_retried() -> None:
    jobs = MemoryJobs(_parsed())

    async def extract(artifact: ParserArtifact) -> ExtractionReport:
        raise VLLMError("down")

    handler = _handler(jobs, MemoryStorage(), extract)
    with pytest.raises(VLLMError):
        handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.status == "extracting"


def test_a_missing_parser_artifact_fails_without_retry() -> None:
    jobs = MemoryJobs(_parsed())
    storage = MemoryStorage()
    storage.payloads.pop(PARSE_REF)

    async def extract(artifact: ParserArtifact) -> ExtractionReport:
        raise AssertionError("model was called")

    handler = _handler(jobs, storage, extract)
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.error == SafeError(code="internal_error")


def test_a_job_still_parsing_is_left_alone() -> None:
    state = JobState(job_id=JOB_ID, status="parsing", parser="default", pdf_ref="pdf/abc")
    jobs = MemoryJobs(state)
    called = False

    async def extract(artifact: ParserArtifact) -> ExtractionReport:
        nonlocal called
        called = True
        raise AssertionError("model was called")

    handler = _handler(jobs, MemoryStorage(), extract)
    handler.handle(_message())
    handler.close()

    assert called is False
    assert jobs.state == state


def test_deferral_expiry_marks_an_extracting_job_failed() -> None:
    jobs = MemoryJobs(_parsed().with_claim("extracting"))
    handler = _handler(jobs, MemoryStorage(), _real_extract(StubVLLMService()))
    handler.on_failed(_message(), JobDeferralExpired())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="internal_error")
    assert jobs.state.parse_result_ref == PARSE_REF


def test_on_failed_records_timeout_and_keeps_the_parser_artifact() -> None:
    jobs = MemoryJobs(_parsed().with_claim("extracting"))
    handler = _handler(jobs, MemoryStorage(), _real_extract(StubVLLMService()))
    handler.on_failed(_message(), JobTimeLimitExceeded())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="timeout")
    assert jobs.state.parse_result_ref == PARSE_REF


def test_second_job_with_the_same_parser_artifact_reuses_the_result() -> None:
    storage = MemoryStorage()
    vllm = StubVLLMService()
    first = MemoryJobs(_parsed())
    handler = _handler(first, storage, _real_extract(vllm))
    handler.handle(_message())
    handler.close()

    other_id = UUID("9d0c6a52-1b7e-4c3f-a2d8-5e6f7a8b9c01")
    second = MemoryJobs(_parsed().with_changes(job_id=other_id))
    handler = _handler(second, storage, _real_extract(vllm))
    handler.handle(ExtractJobMessage(job_id=other_id))
    handler.close()

    assert len(vllm.prompts) == 1
    assert storage.stores == 1
    assert second.state is not None
    assert second.state.status == "done"
    assert second.state.extract_result_ref == RESULT_PATH


def test_parser_artifact_that_cannot_form_a_path_is_internal_error() -> None:
    jobs = MemoryJobs(_parsed())
    storage = MemoryStorage()
    storage.payloads[PARSE_REF] = (
        _parser_artifact().model_copy(update={"checksum": "a/b"}).model_dump_json().encode()
    )

    async def extract(artifact: ParserArtifact) -> ExtractionReport:
        raise AssertionError("model was called")

    handler = _handler(jobs, storage, extract)
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.error == SafeError(code="internal_error")
    assert storage.stores == 0


def test_malformed_message_cannot_fail_a_parsed_job() -> None:
    """`parsed` has no transition to `failed`. The job stays for a valid wake-up."""
    jobs = MemoryJobs(_parsed())
    handler = _handler(jobs, MemoryStorage(), _real_extract(StubVLLMService()))
    handler.on_malformed({"job_id": str(JOB_ID), "extra": "nope"})
    handler.close()

    assert jobs.state is not None
    assert jobs.state.status == "parsed"
    assert jobs.state.error is None
