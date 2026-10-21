import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import UUID

import pytest
from stubs import StubVLLMService

from bioparser.artifact_paths import (
    ARTIFACT_PATH_MAX_LENGTH,
    extract_result_path,
    parse_result_path,
)
from bioparser.extract.backend.default.extractor import extract_observations
from bioparser.extract.backend.default.locate import cited_observations
from bioparser.extract.backend.default.schema import Extraction, ModelObservation
from bioparser.extract.backend.factory import get_extractor
from bioparser.extract.errors import (
    ExtractionFailedError,
    ExtractorBackendUnavailableError,
    ExtractorProcessError,
)
from bioparser.extract.models import ExtractionArtifact, Observation
from bioparser.extract.protocol import Extractor
from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME, EXTRACTOR_VERSIONS
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
RESULT_PATH = extract_result_path(PARSE_REF, "default", "1")
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
        extractor="default",
        pdf_ref="pdf/abc",
        parse_result_ref=PARSE_REF,
    )


def _message() -> ExtractJobMessage:
    return ExtractJobMessage(job_id=JOB_ID)


def _real_extract(
    vllm: StubVLLMService,
) -> Callable[[ParserArtifact], Awaitable[list[Observation]]]:
    service = cast(VLLMService, vllm)

    def extract(artifact: ParserArtifact) -> Awaitable[list[Observation]]:
        async def run() -> list[Observation]:
            result = await extract_observations(service, artifact, char_budget=8000, max_tokens=256)
            return cited_observations(result.output, artifact)

        return run()

    return extract


class _CallableExtractor:
    def __init__(self, extract: Callable[[ParserArtifact], Awaitable[list[Observation]]]) -> None:
        self._extract = extract

    async def extract(self, artifact: ParserArtifact) -> list[Observation]:
        return await self._extract(artifact)


def _factory(
    extract: Callable[[ParserArtifact], Awaitable[list[Observation]]],
) -> Callable[[str], Extractor]:
    def extractor_factory(_name: str) -> Extractor:
        return _CallableExtractor(extract)

    return extractor_factory


class _FailingVLLM(StubVLLMService):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    async def generate_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        system_prompt: str,
        max_tokens: int,
    ) -> str:
        raise self._error


def _registered(vllm: StubVLLMService) -> Callable[[str], Extractor]:
    def extractor_factory(name: str) -> Extractor:
        return get_extractor(
            name,
            vllm=cast(VLLMService, vllm),
            char_budget=8000,
            max_tokens=256,
        )

    return extractor_factory


def _handler(
    jobs: MemoryJobs,
    storage: MemoryStorage,
    extract: Callable[[ParserArtifact], Awaitable[list[Observation]]],
    *,
    max_claims: int = DEFAULT_EXTRACT_MAX_RETRIES + 1,
) -> ExtractJobHandler:
    return ExtractJobHandler(
        jobs=jobs,
        storage=storage,
        extractor_factory=_factory(extract),
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
    value = stored.observations[0].verbatimTraitValue
    assert value.text == "12.5"
    assert value.evidence is not None
    assert QUOTE[value.evidence.start : value.evidence.end] == "12.5"
    assert stored.observations[0].verbatimScientificName.evidence is None
    assert stored.extractor == "default"
    assert stored.extractor_version == "1"
    assert "model" not in stored.model_dump()
    assert "canonical_value" not in stored.model_dump()
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


def test_an_unlocated_value_is_stored_without_a_range() -> None:
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
    assert stored.observations[0].verbatimTraitValue.text == "12.5"
    assert stored.observations[0].verbatimTraitValue.evidence is None
    assert jobs.state is not None
    assert jobs.state.status == "done"


def test_schema_failure_is_permanent() -> None:
    jobs = MemoryJobs(_parsed())

    async def extract(artifact: ParserArtifact) -> list[Observation]:
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
    handler = ExtractJobHandler(
        jobs=jobs,
        storage=MemoryStorage(),
        extractor_factory=_registered(_FailingVLLM(VLLMTruncatedError("cut off"))),
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
    )
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="extraction_failed")
    assert jobs.state.claims("extracting") == 1


def test_a_rejected_request_is_permanent() -> None:
    jobs = MemoryJobs(_parsed())
    handler = ExtractJobHandler(
        jobs=jobs,
        storage=MemoryStorage(),
        extractor_factory=_registered(
            _FailingVLLM(VLLMRequestRejectedError("vLLM rejected the request: BadRequestError"))
        ),
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
    )
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

    async def extract(artifact: ParserArtifact) -> list[Observation]:
        nonlocal called
        called = True
        raise AssertionError("model was called")

    handler = ExtractJobHandler(
        jobs=jobs,
        storage=MemoryStorage(),
        extractor_factory=_factory(extract),
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
        extractor_factory=_factory(_real_extract(vllm)),
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
    handler = ExtractJobHandler(
        jobs=jobs,
        storage=MemoryStorage(),
        extractor_factory=_registered(_FailingVLLM(VLLMError("down"))),
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
    )
    with pytest.raises(ExtractorProcessError):
        handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.status == "extracting"


def test_a_missing_parser_artifact_fails_without_retry() -> None:
    jobs = MemoryJobs(_parsed())
    storage = MemoryStorage()
    storage.payloads.pop(PARSE_REF)

    async def extract(artifact: ParserArtifact) -> list[Observation]:
        raise AssertionError("model was called")

    handler = _handler(jobs, storage, extract)
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.error == SafeError(code="internal_error")


def test_an_invalid_parser_artifact_fails_without_calling_the_model() -> None:
    jobs = MemoryJobs(_parsed())
    storage = MemoryStorage()
    invalid = b'{"old":true}'
    storage.payloads[PARSE_REF] = invalid

    async def extract(artifact: ParserArtifact) -> list[Observation]:
        raise AssertionError("model was called")

    handler = _handler(jobs, storage, extract)
    handler.handle(_message())
    handler.close()

    assert storage.stores == 0
    assert storage.retrieve(PARSE_REF) == invalid
    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="internal_error")


def test_a_job_still_parsing_is_left_alone() -> None:
    state = JobState(
        job_id=JOB_ID, status="parsing", parser="default", extractor="default", pdf_ref="pdf/abc"
    )
    jobs = MemoryJobs(state)
    called = False

    async def extract(artifact: ParserArtifact) -> list[Observation]:
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


def test_an_invalid_stored_extraction_fails_without_calling_the_model() -> None:
    jobs = MemoryJobs(_parsed())
    storage = MemoryStorage()
    invalid = b'{"old":true}'
    storage.payloads[RESULT_PATH] = invalid

    async def extract(artifact: ParserArtifact) -> list[Observation]:
        raise AssertionError("model was called")

    handler = _handler(jobs, storage, extract)
    handler.handle(_message())
    handler.close()

    assert storage.stores == 0
    assert storage.retrieve(RESULT_PATH) == invalid
    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="internal_error")


def test_a_parse_path_that_cannot_take_an_extraction_is_internal_error() -> None:
    suffix = "/extracted/default/1"
    ref = "/".join(("a" * 200, "b" * 200, "c" * 93))
    assert len(ref) <= ARTIFACT_PATH_MAX_LENGTH
    assert len(ref) + len(suffix) > ARTIFACT_PATH_MAX_LENGTH
    jobs = MemoryJobs(_parsed().with_changes(parse_result_ref=ref))
    storage = MemoryStorage()
    storage.payloads[ref] = _parser_artifact().model_dump_json().encode()

    async def extract(artifact: ParserArtifact) -> list[Observation]:
        raise AssertionError("model was called")

    handler = _handler(jobs, storage, extract)
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.error == SafeError(code="internal_error")
    assert storage.stores == 0


def test_two_mineru_tiers_get_two_extractions() -> None:
    basic_text = "Basic tier recorded 12.5 g."
    standard_text = "Standard tier recorded 40.0 g."
    basic_ref = parse_result_path(PDF_CHECKSUM, "mineru", "2", "basic")
    standard_ref = parse_result_path(PDF_CHECKSUM, "mineru", "2", "standard")
    storage = MemoryStorage()
    storage.payloads[basic_ref] = _parser_artifact(basic_text).model_dump_json().encode()
    storage.payloads[standard_ref] = _parser_artifact(standard_text).model_dump_json().encode()
    seen: list[str] = []

    async def extract(artifact: ParserArtifact) -> list[Observation]:
        content = artifact.pages[0].blocks[0].content
        assert isinstance(content, TextContent)
        seen.append(content.text)
        observation = ModelObservation(
            taxon="Mus musculus",
            trait="body_mass",
            value=12.5,
            unit="g",
            evidence_block_id="b0",
            quotation=content.text,
        )
        return cited_observations(Extraction(observations=[observation]), artifact)

    basic = MemoryJobs(_parsed().with_changes(parse_result_ref=basic_ref))
    handler = _handler(basic, storage, extract)
    handler.handle(_message())
    handler.close()

    other_id = UUID("9d0c6a52-1b7e-4c3f-a2d8-5e6f7a8b9c01")
    standard = MemoryJobs(_parsed().with_changes(job_id=other_id, parse_result_ref=standard_ref))
    handler = _handler(standard, storage, extract)
    handler.handle(ExtractJobMessage(job_id=other_id))
    handler.close()

    basic_path = extract_result_path(basic_ref, "default", "1")
    standard_path = extract_result_path(standard_ref, "default", "1")
    assert basic.state is not None and standard.state is not None
    assert basic.state.extract_result_ref == basic_path
    assert standard.state.extract_result_ref == standard_path
    assert basic_path != standard_path
    assert seen == [basic_text, standard_text]
    basic_value = (
        ExtractionArtifact.model_validate_json(storage.retrieve(basic_path))
        .observations[0]
        .verbatimTraitValue
    )
    assert basic_value.evidence is not None
    assert basic_text[basic_value.evidence.start : basic_value.evidence.end] == "12.5"
    standard_value = (
        ExtractionArtifact.model_validate_json(storage.retrieve(standard_path))
        .observations[0]
        .verbatimTraitValue
    )
    assert standard_value.text == "12.5"
    assert standard_value.evidence is None


def test_worker_runs_the_extractor_named_on_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bioparser.jobstate.models.EXTRACTORS", ("default", "other"))
    monkeypatch.setattr(
        "bioparser.worker.extract.handler.EXTRACTOR_VERSIONS",
        {"other": "4"},
    )
    jobs = MemoryJobs(_parsed().with_changes(extractor="other"))
    storage = MemoryStorage()
    seen: list[str] = []

    async def extract(artifact: ParserArtifact) -> list[Observation]:
        return []

    def extractor_factory(name: str) -> Extractor:
        seen.append(name)
        return _CallableExtractor(extract)

    handler = ExtractJobHandler(
        jobs=jobs,
        storage=storage,
        extractor_factory=extractor_factory,
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
    )
    handler.handle(_message())
    handler.close()

    assert seen == ["other"]
    assert jobs.state is not None
    assert jobs.state.extract_result_ref == extract_result_path(PARSE_REF, "other", "4")


def test_registered_extractor_stores_the_resolved_version() -> None:
    jobs = MemoryJobs(_parsed())
    storage = MemoryStorage()
    vllm = StubVLLMService()
    seen: list[str] = []

    def extractor_factory(name: str) -> Extractor:
        seen.append(name)
        return get_extractor(
            name,
            vllm=cast(VLLMService, vllm),
            char_budget=8000,
            max_tokens=256,
        )

    handler = ExtractJobHandler(
        jobs=jobs,
        storage=storage,
        extractor_factory=extractor_factory,
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
    )
    handler.handle(_message())
    handler.close()

    version = EXTRACTOR_VERSIONS[DEFAULT_EXTRACTOR_NAME]
    assert seen == [DEFAULT_EXTRACTOR_NAME]
    assert version == "1"
    assert jobs.state is not None
    assert jobs.state.status == "done"
    assert jobs.state.extract_result_ref == extract_result_path(
        PARSE_REF, DEFAULT_EXTRACTOR_NAME, version
    )
    assert vllm.prompts


def test_artifact_path_uses_the_version_resolved_for_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "bioparser.worker.extract.handler.EXTRACTOR_VERSIONS",
        {DEFAULT_EXTRACTOR_NAME: "4"},
    )
    jobs = MemoryJobs(_parsed())
    storage = MemoryStorage()
    handler = _handler(jobs, storage, _real_extract(StubVLLMService()))
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.extract_result_ref == extract_result_path(
        PARSE_REF, DEFAULT_EXTRACTOR_NAME, "4"
    )


def test_an_extractor_with_no_version_is_internal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bioparser.worker.extract.handler.EXTRACTOR_VERSIONS", {})
    jobs = MemoryJobs(_parsed())
    storage = MemoryStorage()

    async def extract(artifact: ParserArtifact) -> list[Observation]:
        raise AssertionError("model was called")

    handler = _handler(jobs, storage, extract)
    handler.handle(_message())
    handler.close()

    assert storage.stores == 0
    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="internal_error")


def test_an_extractor_that_cannot_run_is_internal_error() -> None:
    jobs = MemoryJobs(_parsed())
    storage = MemoryStorage()

    class _Down:
        async def extract(self, artifact: ParserArtifact) -> list[Observation]:
            raise ExtractorBackendUnavailableError("cannot run")

    handler = ExtractJobHandler(
        jobs=jobs,
        storage=storage,
        extractor_factory=lambda _name: _Down(),
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
    )
    handler.handle(_message())
    handler.close()

    assert storage.stores == 0
    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="internal_error")


def test_an_unknown_extractor_is_internal_error() -> None:
    jobs = MemoryJobs(_parsed())
    storage = MemoryStorage()

    def extractor_factory(name: str) -> Extractor:
        raise ExtractorBackendUnavailableError(f"Unknown extractor {name!r}.")

    handler = ExtractJobHandler(
        jobs=jobs,
        storage=storage,
        extractor_factory=extractor_factory,
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
    )
    handler.handle(_message())
    handler.close()

    assert storage.stores == 0
    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="internal_error")


def test_malformed_message_cannot_fail_a_parsed_job() -> None:
    """`parsed` has no transition to `failed`. The job stays for a valid wake-up."""
    jobs = MemoryJobs(_parsed())
    handler = _handler(jobs, MemoryStorage(), _real_extract(StubVLLMService()))
    handler.on_malformed({"job_id": str(JOB_ID), "extra": "nope"})
    handler.close()

    assert jobs.state is not None
    assert jobs.state.status == "parsed"
    assert jobs.state.error is None
