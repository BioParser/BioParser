import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, cast
from uuid import UUID

import pytest
from stubs import (
    MemoryStorage,
    StubJobs,
    StubVLLMService,
    bind_jobs,
    clear_jobs,
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
from bioparser.extract.models import (
    Citation,
    ExtractionArtifact,
    Interpretation,
    Observation,
    TextRange,
)
from bioparser.extract.protocol import Extractor
from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME, EXTRACTOR_VERSIONS
from bioparser.jobs import JobState
from bioparser.jobs.queue import (
    ExtractJobMessage,
    JobDeferralExpired,
    JobDeferred,
    JobTimeLimitExceeded,
)
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
from bioparser.storage.artifact_paths import (
    ARTIFACT_PATH_MAX_LENGTH,
    extract_result_path,
    parse_result_path,
)
from bioparser.worker.extract.config import DEFAULT_EXTRACT_MAX_RETRIES
from bioparser.worker.extract.handler import ExtractJobHandler

JOB_ID = UUID("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e34")
PARSE_REF = "6b1f0c2a-7d44-4e1a-9c3b-2f8e5a0d6c11"
PDF_CHECKSUM = "a" * 64
RESULT_PATH = extract_result_path(PARSE_REF, "default", "1")
QUOTE = "Mus musculus adults averaged 12.5 kg in the sampled population."


def _stub() -> StubVLLMService:
    """A model answer whose name, value and unit each occur once in the quotation."""
    return StubVLLMService(
        observations=[
            {
                "taxon": "Mus musculus",
                "trait": "body_mass",
                "value": 12.5,
                "unit": "kg",
                "evidence_block_id": "b0",
                "quotation": QUOTE,
            }
        ]
    )


@pytest.fixture(autouse=True)
def _stub_job_queries(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    bind_jobs(monkeypatch)
    yield
    clear_jobs()


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


def _storage() -> MemoryStorage:
    return MemoryStorage({PARSE_REF: _parser_artifact().model_dump_json().encode()})


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


def _artifact(
    observations: list[Observation],
    *,
    extractor: str = DEFAULT_EXTRACTOR_NAME,
    extractor_version: str = EXTRACTOR_VERSIONS[DEFAULT_EXTRACTOR_NAME],
) -> ExtractionArtifact:
    return ExtractionArtifact(
        extractor=extractor, extractor_version=extractor_version, observations=observations
    )


def _real_extract(
    vllm: StubVLLMService,
) -> Callable[[ParserArtifact], Awaitable[ExtractionArtifact]]:
    service = cast(VLLMService, vllm)

    def extract(artifact: ParserArtifact) -> Awaitable[ExtractionArtifact]:
        async def run() -> ExtractionArtifact:
            result = await extract_observations(service, artifact, char_budget=8000, max_tokens=256)
            return _artifact(cited_observations(result.output, artifact))

        return run()

    return extract


class _CallableExtractor:
    def __init__(self, extract: Callable[[ParserArtifact], Awaitable[ExtractionArtifact]]) -> None:
        self._extract = extract

    async def extract(self, artifact: ParserArtifact) -> ExtractionArtifact:
        return await self._extract(artifact)


def _factory(
    extract: Callable[[ParserArtifact], Awaitable[ExtractionArtifact]],
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
    jobs: StubJobs,
    storage: MemoryStorage,
    extract: Callable[[ParserArtifact], Awaitable[ExtractionArtifact]],
    *,
    max_claims: int = DEFAULT_EXTRACT_MAX_RETRIES + 1,
) -> ExtractJobHandler:
    return ExtractJobHandler(
        database_url="postgresql://unused",
        storage=storage,
        extractor_factory=_factory(extract),
        max_claims=max_claims,
    )


def test_success_stores_the_report_and_marks_done() -> None:
    jobs = StubJobs(_parsed())
    storage = _storage()
    vllm = _stub()
    handler = _handler(jobs, storage, _real_extract(vllm))
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert jobs.state is not None
    assert jobs.state.status == "done"
    assert jobs.state.extract_result_ref == RESULT_PATH
    assert jobs.state.parse_result_ref == PARSE_REF
    assert jobs.state.claims("extracting") == 1
    stored = ExtractionArtifact.model_validate_json(asyncio.run(storage.retrieve(RESULT_PATH)))
    value = stored.observations[0].verbatimTraitValue
    assert value.text == "12.5"
    assert value.evidence is not None
    assert QUOTE[value.evidence[0].start : value.evidence[0].end] == "12.5"
    assert stored.observations[0].verbatimScientificName.text == "Mus musculus"
    assert stored.extractor == "default"
    assert stored.extractor_version == "1"
    assert "model" not in stored.model_dump()
    assert "canonical_value" not in stored.model_dump()
    assert storage.metadata[RESULT_PATH].created_by == "extractor-worker"
    assert vllm.prompts


def test_duplicate_delivery_does_not_call_the_model_again() -> None:
    jobs = StubJobs(_parsed())
    storage = _storage()
    vllm = _stub()
    handler = _handler(jobs, storage, _real_extract(vllm))
    message = _message()
    asyncio.run(handler.handle(message))
    prompts = len(vllm.prompts)
    asyncio.run(handler.handle(message))
    asyncio.run(handler.aclose())

    assert len(vllm.prompts) == prompts
    assert storage.stores == 1
    assert jobs.state is not None
    assert jobs.state.status == "done"


def test_an_unlocated_observation_is_not_stored() -> None:
    jobs = StubJobs(_parsed())
    storage = _storage()
    vllm = StubVLLMService(
        observations=[
            {
                "taxon": "Mus musculus",
                "trait": "body_mass",
                "value": 12.5,
                "unit": "kg",
                "evidence_block_id": "b0",
                "quotation": "not in the paper",
            }
        ]
    )
    handler = _handler(jobs, storage, _real_extract(vllm))
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    stored = ExtractionArtifact.model_validate_json(asyncio.run(storage.retrieve(RESULT_PATH)))
    assert stored.observations == []
    assert jobs.state is not None
    assert jobs.state.status == "done"


def test_schema_failure_is_permanent() -> None:
    jobs = StubJobs(_parsed())

    async def extract(artifact: ParserArtifact) -> ExtractionArtifact:
        raise ExtractionFailedError("schema")

    handler = _handler(jobs, _storage(), extract)
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error_code == "extraction_failed"
    assert jobs.state.parse_result_ref == PARSE_REF


def test_truncated_output_is_permanent() -> None:
    jobs = StubJobs(_parsed())
    handler = ExtractJobHandler(
        database_url="postgresql://unused",
        storage=_storage(),
        extractor_factory=_registered(_FailingVLLM(VLLMTruncatedError("cut off"))),
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
    )
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error_code == "extraction_failed"
    assert jobs.state.claims("extracting") == 1


def test_a_rejected_request_is_permanent() -> None:
    jobs = StubJobs(_parsed())
    handler = ExtractJobHandler(
        database_url="postgresql://unused",
        storage=_storage(),
        extractor_factory=_registered(
            _FailingVLLM(VLLMRequestRejectedError("vLLM rejected the request: BadRequestError"))
        ),
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
    )
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error_code == "extraction_failed"
    assert jobs.state.claims("extracting") == 1


def test_vllm_down_leaves_the_job_parsed(caplog: pytest.LogCaptureFixture) -> None:
    jobs = StubJobs(_parsed())
    vllm = _stub()
    vllm.up = False
    called = False

    async def extract(artifact: ParserArtifact) -> ExtractionArtifact:
        nonlocal called
        called = True
        raise AssertionError("model was called")

    handler = ExtractJobHandler(
        database_url="postgresql://unused",
        storage=_storage(),
        extractor_factory=_factory(extract),
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
        vllm=cast(VLLMService, vllm),
    )
    with (
        caplog.at_level(logging.WARNING, logger="bioparser.worker.extract.handler"),
        pytest.raises(JobDeferred),
    ):
        asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert called is False
    assert "vLLM is not answering" in caplog.text
    assert jobs.state is not None
    assert jobs.state.status == "parsed"
    assert jobs.state.claims("extracting") == 0


def test_vllm_answering_still_extracts() -> None:
    jobs = StubJobs(_parsed())
    storage = _storage()
    vllm = _stub()
    handler = ExtractJobHandler(
        database_url="postgresql://unused",
        storage=storage,
        extractor_factory=_factory(_real_extract(vllm)),
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
        vllm=cast(VLLMService, vllm),
    )
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert jobs.state is not None
    assert jobs.state.status == "done"
    assert vllm.prompts


def test_on_failed_on_a_parsed_job_says_it_cannot_be_marked_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    jobs = StubJobs(_parsed())
    handler = _handler(jobs, _storage(), _real_extract(_stub()))
    with caplog.at_level(logging.INFO, logger="bioparser.worker.stage"):
        asyncio.run(handler.on_failed(_message(), RuntimeError("wake-up failed")))
    asyncio.run(handler.aclose())

    assert jobs.state is not None
    assert jobs.state.status == "parsed"
    assert "cannot be marked failed" in caplog.text
    assert "already finished or moved on" not in caplog.text


def test_vllm_errors_are_retried() -> None:
    jobs = StubJobs(_parsed())
    handler = ExtractJobHandler(
        database_url="postgresql://unused",
        storage=_storage(),
        extractor_factory=_registered(_FailingVLLM(VLLMError("down"))),
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
    )
    with pytest.raises(ExtractorProcessError):
        asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert jobs.state is not None
    assert jobs.state.status == "extracting"


def test_a_missing_parser_artifact_fails_without_retry() -> None:
    jobs = StubJobs(_parsed())
    storage = _storage()
    storage.payloads.pop(PARSE_REF)

    async def extract(artifact: ParserArtifact) -> ExtractionArtifact:
        raise AssertionError("model was called")

    handler = _handler(jobs, storage, extract)
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert jobs.state is not None
    assert jobs.state.error_code == "internal_error"


def test_an_invalid_parser_artifact_fails_without_calling_the_model() -> None:
    jobs = StubJobs(_parsed())
    storage = _storage()
    invalid = b'{"old":true}'
    storage.payloads[PARSE_REF] = invalid

    async def extract(artifact: ParserArtifact) -> ExtractionArtifact:
        raise AssertionError("model was called")

    handler = _handler(jobs, storage, extract)
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert storage.stores == 0
    assert asyncio.run(storage.retrieve(PARSE_REF)) == invalid
    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error_code == "internal_error"


def test_a_job_still_parsing_is_left_alone() -> None:
    state = JobState(
        job_id=JOB_ID, status="parsing", parser="default", extractor="default", pdf_ref="pdf/abc"
    )
    jobs = StubJobs(state)
    called = False

    async def extract(artifact: ParserArtifact) -> ExtractionArtifact:
        nonlocal called
        called = True
        raise AssertionError("model was called")

    handler = _handler(jobs, _storage(), extract)
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert called is False
    assert jobs.state == state


def test_deferral_expiry_marks_an_extracting_job_failed() -> None:
    jobs = StubJobs(_parsed().with_changes(status="extracting", extracting_claims=1))
    handler = _handler(jobs, _storage(), _real_extract(_stub()))
    asyncio.run(handler.on_failed(_message(), JobDeferralExpired()))
    asyncio.run(handler.aclose())

    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error_code == "internal_error"
    assert jobs.state.parse_result_ref == PARSE_REF


def test_on_failed_records_timeout_and_keeps_the_parser_artifact() -> None:
    jobs = StubJobs(_parsed().with_changes(status="extracting", extracting_claims=1))
    handler = _handler(jobs, _storage(), _real_extract(_stub()))
    asyncio.run(handler.on_failed(_message(), JobTimeLimitExceeded()))
    asyncio.run(handler.aclose())

    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error_code == "timeout"
    assert jobs.state.parse_result_ref == PARSE_REF


def test_second_job_with_the_same_parser_artifact_reuses_the_result() -> None:
    storage = _storage()
    vllm = _stub()
    first = StubJobs(_parsed())
    handler = _handler(first, storage, _real_extract(vllm))
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    other_id = UUID("9d0c6a52-1b7e-4c3f-a2d8-5e6f7a8b9c01")
    second = StubJobs(_parsed().with_changes(job_id=other_id))
    handler = _handler(second, storage, _real_extract(vllm))
    asyncio.run(handler.handle(ExtractJobMessage(job_id=other_id)))
    asyncio.run(handler.aclose())

    assert len(vllm.prompts) == 1
    assert storage.stores == 1
    assert second.state is not None
    assert second.state.status == "done"
    assert second.state.extract_result_ref == RESULT_PATH


def test_an_invalid_stored_extraction_fails_without_calling_the_model() -> None:
    jobs = StubJobs(_parsed())
    storage = _storage()
    invalid = b'{"old":true}'
    storage.payloads[RESULT_PATH] = invalid

    async def extract(artifact: ParserArtifact) -> ExtractionArtifact:
        raise AssertionError("model was called")

    handler = _handler(jobs, storage, extract)
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert storage.stores == 0
    assert asyncio.run(storage.retrieve(RESULT_PATH)) == invalid
    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error_code == "internal_error"


def test_a_parse_path_that_cannot_take_an_extraction_is_internal_error() -> None:
    suffix = "/extracted/default/1"
    ref = "/".join(("a" * 200, "b" * 200, "c" * 93))
    assert len(ref) <= ARTIFACT_PATH_MAX_LENGTH
    assert len(ref) + len(suffix) > ARTIFACT_PATH_MAX_LENGTH
    jobs = StubJobs(_parsed().with_changes(parse_result_ref=ref))
    storage = _storage()
    storage.payloads[ref] = _parser_artifact().model_dump_json().encode()

    async def extract(artifact: ParserArtifact) -> ExtractionArtifact:
        raise AssertionError("model was called")

    handler = _handler(jobs, storage, extract)
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert jobs.state is not None
    assert jobs.state.error_code == "internal_error"
    assert storage.stores == 0


def test_two_mineru_tiers_get_two_extractions() -> None:
    basic_text = "Mus musculus basic tier recorded 12.5 kg."
    standard_text = "Mus musculus standard tier recorded 40.0 kg."
    basic_ref = parse_result_path(PDF_CHECKSUM, "mineru", "2", "basic")
    standard_ref = parse_result_path(PDF_CHECKSUM, "mineru", "2", "standard")
    storage = _storage()
    storage.payloads[basic_ref] = _parser_artifact(basic_text).model_dump_json().encode()
    storage.payloads[standard_ref] = _parser_artifact(standard_text).model_dump_json().encode()
    seen: list[str] = []

    async def extract(artifact: ParserArtifact) -> ExtractionArtifact:
        content = artifact.pages[0].blocks[0].content
        assert isinstance(content, TextContent)
        seen.append(content.text)
        observation = ModelObservation(
            taxon="Mus musculus",
            trait="body_mass",
            value=12.5,
            unit="kg",
            evidence_block_id="b0",
            quotation=content.text,
        )
        return _artifact(cited_observations(Extraction(observations=[observation]), artifact))

    basic = StubJobs(_parsed().with_changes(parse_result_ref=basic_ref))
    handler = _handler(basic, storage, extract)
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    other_id = UUID("9d0c6a52-1b7e-4c3f-a2d8-5e6f7a8b9c01")
    standard = StubJobs(_parsed().with_changes(job_id=other_id, parse_result_ref=standard_ref))
    handler = _handler(standard, storage, extract)
    asyncio.run(handler.handle(ExtractJobMessage(job_id=other_id)))
    asyncio.run(handler.aclose())

    basic_path = extract_result_path(basic_ref, "default", "1")
    standard_path = extract_result_path(standard_ref, "default", "1")
    assert basic.state is not None and standard.state is not None
    assert basic.state.extract_result_ref == basic_path
    assert standard.state.extract_result_ref == standard_path
    assert basic_path != standard_path
    assert seen == [basic_text, standard_text]
    basic_value = (
        ExtractionArtifact.model_validate_json(asyncio.run(storage.retrieve(basic_path)))
        .observations[0]
        .verbatimTraitValue
    )
    assert basic_value.evidence is not None
    assert basic_text[basic_value.evidence[0].start : basic_value.evidence[0].end] == "12.5"
    standard_result = ExtractionArtifact.model_validate_json(
        asyncio.run(storage.retrieve(standard_path))
    )
    assert standard_result.observations == []


def test_worker_runs_the_extractor_named_on_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bioparser.jobs.state.EXTRACTORS", ("default", "other"))
    monkeypatch.setattr(
        "bioparser.worker.extract.handler.EXTRACTOR_VERSIONS",
        {"other": "4"},
    )
    jobs = StubJobs(_parsed().with_changes(extractor="other"))
    storage = _storage()
    seen: list[str] = []

    async def extract(artifact: ParserArtifact) -> ExtractionArtifact:
        return _artifact([], extractor="other", extractor_version="4")

    def extractor_factory(name: str) -> Extractor:
        seen.append(name)
        return _CallableExtractor(extract)

    handler = ExtractJobHandler(
        database_url="postgresql://unused",
        storage=storage,
        extractor_factory=extractor_factory,
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
    )
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert seen == ["other"]
    assert jobs.state is not None
    assert jobs.state.extract_result_ref == extract_result_path(PARSE_REF, "other", "4")


def _range(start: int, end: int, text: str) -> Citation:
    return Citation(text=text, evidence=[TextRange(block_id="b0", start=start, end=end)])


def _observation_with_range(start: int, end: int, text: str) -> Observation:
    return Observation(
        trait=Interpretation(value="body_mass", passage=_range(0, len(QUOTE), QUOTE)),
        verbatimScientificName=_range(0, 12, "Mus musculus"),
        verbatimTraitValue=_range(start, end, text),
        verbatimTraitUnit=_range(QUOTE.index("kg"), QUOTE.index("kg") + 2, "kg"),
    )


def _failed_with(returned: ExtractionArtifact) -> MemoryStorage:
    jobs = StubJobs(_parsed())
    storage = _storage()

    async def extract(artifact: ParserArtifact) -> ExtractionArtifact:
        return returned

    handler = _handler(jobs, storage, extract)
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error_code == "extraction_failed"
    assert RESULT_PATH not in storage.payloads
    return storage


def test_a_range_that_is_not_its_slice_fails_the_job_for_any_extractor() -> None:
    start = QUOTE.index("12.5")
    _failed_with(_artifact([_observation_with_range(start + 1, start + 5, "12.5")]))


def test_an_artifact_for_another_extractor_fails_the_job() -> None:
    _failed_with(_artifact([], extractor="other"))
    _failed_with(_artifact([], extractor_version="2"))


def test_registered_extractor_stores_the_resolved_version() -> None:
    jobs = StubJobs(_parsed())
    storage = _storage()
    vllm = _stub()
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
        database_url="postgresql://unused",
        storage=storage,
        extractor_factory=extractor_factory,
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
    )
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

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
    jobs = StubJobs(_parsed())
    storage = _storage()

    async def extract(artifact: ParserArtifact) -> ExtractionArtifact:
        return _artifact([], extractor_version="4")

    handler = _handler(jobs, storage, extract)
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert jobs.state is not None
    assert jobs.state.extract_result_ref == extract_result_path(
        PARSE_REF, DEFAULT_EXTRACTOR_NAME, "4"
    )


def test_an_extractor_with_no_version_is_internal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bioparser.worker.extract.handler.EXTRACTOR_VERSIONS", {})
    jobs = StubJobs(_parsed())
    storage = _storage()

    async def extract(artifact: ParserArtifact) -> ExtractionArtifact:
        raise AssertionError("model was called")

    handler = _handler(jobs, storage, extract)
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert storage.stores == 0
    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error_code == "internal_error"


def test_an_extractor_that_cannot_run_is_internal_error() -> None:
    jobs = StubJobs(_parsed())
    storage = _storage()

    class _Down:
        async def extract(self, artifact: ParserArtifact) -> ExtractionArtifact:
            raise ExtractorBackendUnavailableError("cannot run")

    handler = ExtractJobHandler(
        database_url="postgresql://unused",
        storage=storage,
        extractor_factory=lambda _name: _Down(),
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
    )
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert storage.stores == 0
    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error_code == "internal_error"


def test_an_unknown_extractor_is_internal_error() -> None:
    jobs = StubJobs(_parsed())
    storage = _storage()

    def extractor_factory(name: str) -> Extractor:
        raise ExtractorBackendUnavailableError(f"Unknown extractor {name!r}.")

    handler = ExtractJobHandler(
        database_url="postgresql://unused",
        storage=storage,
        extractor_factory=extractor_factory,
        max_claims=DEFAULT_EXTRACT_MAX_RETRIES + 1,
    )
    asyncio.run(handler.handle(_message()))
    asyncio.run(handler.aclose())

    assert storage.stores == 0
    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error_code == "internal_error"


def test_malformed_message_cannot_fail_a_parsed_job() -> None:
    """`parsed` has no transition to `failed`. The job stays for a valid wake-up."""
    jobs = StubJobs(_parsed())
    handler = _handler(jobs, _storage(), _real_extract(_stub()))
    asyncio.run(handler.on_malformed({"job_id": str(JOB_ID), "extra": "nope"}))
    asyncio.run(handler.aclose())

    assert jobs.state is not None
    assert jobs.state.status == "parsed"
    assert jobs.state.error_code is None
