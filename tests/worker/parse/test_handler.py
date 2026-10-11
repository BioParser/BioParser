import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import pytest
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware.time_limit import TimeLimit
from stubs import fake_mineru

from bioparser.artifact_paths import parse_result_path
from bioparser.jobqueue import JobTimeLimitExceeded
from bioparser.jobqueue.dramatiq import DramatiqJobQueue
from bioparser.jobqueue.messages import ParseJobMessage
from bioparser.jobstate.errors import (
    CorruptJobStateError,
    JobNotFoundError,
    StaleJobStateWriteError,
)
from bioparser.jobstate.models import ALLOWED_TRANSITIONS, JobState, SafeError
from bioparser.logging_config import setup_logging
from bioparser.parser.backend.factory import get_parser
from bioparser.parser.errors import (
    ParserBackendUnavailableError,
    ParserProcessError,
    ParserTimeoutError,
    UnsupportedDocumentError,
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
from bioparser.parser.protocol import PdfParser
from bioparser.storage import StoredArtifact
from bioparser.storage.errors import ArtifactAlreadyExistsError, ArtifactNotFoundError
from bioparser.storage.models import ArtifactMetadata
from bioparser.worker.parse.config import DEFAULT_PARSE_MAX_RETRIES
from bioparser.worker.parse.handler import ParseJobHandler

JOB_ID = UUID("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e34")
PDF_REF = (
    "6b1f0c2a-7d44-4e1a-9c3b-2f8e5a0d6c11"  # kept as a UUID: the worker doesn't depend on its shape
)
PDF_BYTES = b"%PDF-1.4"
PDF_CHECKSUM = hashlib.sha256(PDF_BYTES).hexdigest()
RESULT_PATH = parse_result_path(PDF_CHECKSUM, "default", "1")


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
        self.payloads: dict[str, bytes] = {PDF_REF: PDF_BYTES}
        self.metadata: dict[str, ArtifactMetadata] = {}
        self.stores = 0

    def store(
        self,
        content: bytes,
        metadata: ArtifactMetadata,
    ) -> str:
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


class RecordingParser:
    def __init__(
        self,
        artifact: ParserArtifact,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.artifact = artifact
        self.error = error
        self.paths: list[bytes] = []

    def parse(self, path: Path) -> ParserArtifact:
        self.paths.append(path.read_bytes())
        if self.error is not None:
            raise self.error
        return self.artifact


def _artifact() -> ParserArtifact:
    return ParserArtifact(
        schema_version=PARSER_ARTIFACT_SCHEMA_VERSION,
        checksum="abc",
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
                        content=TextContent(text="Hello"),
                    )
                ],
            )
        ],
    )


def _queued() -> JobState:
    return JobState(
        job_id=JOB_ID,
        status="queued",
        parser="default",
        pdf_ref=PDF_REF,
    )


def _message(**overrides: object) -> ParseJobMessage:
    data: dict[str, object] = {
        "job_id": str(JOB_ID),
    }
    data.update(overrides)
    return ParseJobMessage.model_validate(data)


def _handler(
    jobs: MemoryJobs,
    storage: MemoryStorage,
    parser: RecordingParser,
    *,
    factory: Callable[[str], PdfParser] | None = None,
    max_claims: int = DEFAULT_PARSE_MAX_RETRIES + 1,
    enqueue: Callable[[UUID], None] | None = None,
) -> ParseJobHandler:
    def _factory(name: str) -> PdfParser:
        if name != "default":
            raise ParserBackendUnavailableError(name)
        return parser

    return ParseJobHandler(
        jobs=jobs,
        storage=storage,
        parser_factory=factory or _factory,
        max_claims=max_claims,
        enqueue_extraction=enqueue,
    )


def test_success_stores_artifact_and_marks_parsed() -> None:
    jobs = MemoryJobs(_queued())
    storage = MemoryStorage()
    parser = RecordingParser(_artifact())
    handler = _handler(jobs, storage, parser)
    handler.handle(_message())
    handler.close()

    assert parser.paths == [b"%PDF-1.4"]
    assert jobs.state is not None
    assert jobs.state.status == "parsed"
    assert jobs.state.parse_result_ref == RESULT_PATH
    assert jobs.state.claims("parsing") == 1
    stored = storage.retrieve(RESULT_PATH)
    restored = ParserArtifact.model_validate_json(stored)
    assert restored == _artifact()
    assert storage.metadata[RESULT_PATH].created_by == "parser-worker"


def test_success_wakes_the_extractor_and_a_duplicate_wakes_it_again() -> None:
    jobs = MemoryJobs(_queued())
    woken: list[UUID] = []
    handler = _handler(
        jobs,
        MemoryStorage(),
        RecordingParser(_artifact()),
        enqueue=woken.append,
    )
    message = _message()
    handler.handle(message)
    handler.handle(message)
    handler.close()

    assert woken == [JOB_ID, JOB_ID]
    assert jobs.state is not None
    assert jobs.state.status == "parsed"


def test_a_lost_wake_up_is_sent_again_on_the_next_delivery() -> None:
    jobs = MemoryJobs(_queued())
    calls = 0

    def _enqueue(job_id: UUID) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("queue down")

    handler = _handler(jobs, MemoryStorage(), RecordingParser(_artifact()), enqueue=_enqueue)
    message = _message()
    with pytest.raises(RuntimeError, match="queue down"):
        handler.handle(message)
    assert jobs.state is not None
    assert jobs.state.status == "parsed"

    handler.handle(message)
    handler.close()
    assert calls == 2
    assert jobs.state.status == "parsed"


def test_duplicate_delivery_does_not_parse_again() -> None:
    jobs = MemoryJobs(_queued())
    storage = MemoryStorage()
    parser = RecordingParser(_artifact())
    handler = _handler(jobs, storage, parser)
    message = _message()
    handler.handle(message)
    stored = storage.retrieve(RESULT_PATH)
    handler.handle(message)
    handler.close()

    assert parser.paths == [b"%PDF-1.4"]
    assert storage.stores == 1
    assert storage.retrieve(RESULT_PATH) == stored
    assert jobs.state is not None
    assert jobs.state.status == "parsed"


def test_claim_is_counted_and_a_further_start_stops_at_the_cap() -> None:
    jobs = MemoryJobs(_queued())
    storage = MemoryStorage()
    parser = RecordingParser(_artifact(), error=RuntimeError("backend blip"))
    handler = _handler(jobs, storage, parser, max_claims=1)
    with pytest.raises(RuntimeError, match="backend blip"):
        handler.handle(_message())
    assert jobs.state is not None
    assert jobs.state.claims("parsing") == 1

    parser.error = None
    handler.handle(_message())
    handler.close()

    assert len(parser.paths) == 1
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="internal_error")
    assert jobs.state.claims("parsing") == 1


def test_retry_after_parser_error_can_succeed() -> None:
    jobs = MemoryJobs(_queued())
    storage = MemoryStorage()
    parser = RecordingParser(_artifact(), error=RuntimeError("backend blip"))
    handler = _handler(jobs, storage, parser)
    message = _message()
    with pytest.raises(RuntimeError, match="backend blip"):
        handler.handle(message)
    assert jobs.state is not None
    assert jobs.state.status == "parsing"

    parser.error = None
    handler.handle(message)
    handler.close()
    assert jobs.state.status == "parsed"  # type: ignore[comparison-overlap]
    assert jobs.state.claims("parsing") == 2
    assert len(parser.paths) == 2


def test_parser_comes_from_job_state() -> None:
    jobs = MemoryJobs(_queued().with_changes(parser="mineru"))
    seen: list[str] = []

    def _factory(name: str) -> PdfParser:
        seen.append(name)
        raise ParserBackendUnavailableError(name)

    handler = ParseJobHandler(
        jobs=jobs,
        storage=MemoryStorage(),
        parser_factory=_factory,
        max_claims=DEFAULT_PARSE_MAX_RETRIES + 1,
    )
    handler.handle(_message())
    handler.close()

    assert seen == ["mineru"]
    assert jobs.state is not None
    assert jobs.state.error == SafeError(code="parse_failed")


def test_malformed_message_fails_the_job() -> None:
    jobs = MemoryJobs(_queued())
    handler = _handler(jobs, MemoryStorage(), RecordingParser(_artifact()))
    handler.on_malformed({"job_id": str(JOB_ID), "extra": "nope"})
    handler.close()
    assert jobs.state is not None
    assert jobs.state.error == SafeError(code="internal_error")


def test_unsupported_document_is_invalid_pdf() -> None:
    jobs = MemoryJobs(_queued())
    storage = MemoryStorage()
    parser = RecordingParser(_artifact(), error=UnsupportedDocumentError("empty"))
    handler = _handler(jobs, storage, parser)
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.error == SafeError(code="invalid_pdf")


def test_missing_input_fails_without_retry() -> None:
    jobs = MemoryJobs(_queued())
    storage = MemoryStorage()
    storage.payloads.clear()
    parser = RecordingParser(_artifact())
    handler = _handler(jobs, storage, parser)
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.error == SafeError(code="internal_error")
    assert parser.paths == []


def test_parser_without_a_version_is_internal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bioparser.worker.parse.handler.PARSER_VERSIONS", {})
    jobs = MemoryJobs(_queued())
    parser = RecordingParser(_artifact())
    handler = _handler(jobs, MemoryStorage(), parser)
    handler.handle(_message())
    handler.close()

    assert parser.paths == []
    assert jobs.state is not None
    assert jobs.state.error == SafeError(code="internal_error")


def test_unavailable_parser_fails_without_retry() -> None:
    jobs = MemoryJobs(_queued().with_changes(parser="mineru"))
    parser = RecordingParser(_artifact())
    handler = _handler(jobs, MemoryStorage(), parser)
    handler.handle(_message())
    handler.close()

    assert parser.paths == []
    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="parse_failed")


def test_a_mineru_failure_is_retried_not_failed_as_invalid_pdf() -> None:
    """MinerU's catch-all parse_failed, through the real client, is not a judged document."""
    entry = {"name": "a.pdf", "status": "failed", "error": {"code": "parse_failed"}}
    reply = (409, {"task_id": "job_1", "status": "failed", "files": [entry]})

    jobs = MemoryJobs(_queued().with_changes(parser="mineru"))
    with fake_mineru(reply) as (url, _seen):
        handler = ParseJobHandler(
            jobs=jobs,
            storage=MemoryStorage(),
            parser_factory=lambda name: get_parser(name, timeout_s=5.0, mineru_base_url=url),
            max_claims=DEFAULT_PARSE_MAX_RETRIES + 1,
        )
        with pytest.raises(ParserProcessError, match="parse_failed"):
            handler.handle(_message())
        handler.close()

    assert jobs.state is not None
    assert jobs.state.status == "parsing"


@pytest.mark.parametrize("error", [ParserTimeoutError("too slow"), JobTimeLimitExceeded()])
def test_parser_timeout_fails_with_timeout_without_retry(error: BaseException) -> None:
    """Both the parser's own limit and the queue's time limit end the job as a timeout."""

    class _SlowParser:
        def parse(self, path: Path) -> ParserArtifact:
            raise error

    jobs = MemoryJobs(_queued())
    handler = _handler(
        jobs, MemoryStorage(), RecordingParser(_artifact()), factory=lambda _name: _SlowParser()
    )
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="timeout")


def test_invalid_existing_artifact_fails_the_job() -> None:
    jobs = MemoryJobs(_queued())
    storage = MemoryStorage()
    invalid = b'{"old":true}'
    storage.payloads[RESULT_PATH] = invalid
    parser = RecordingParser(_artifact())
    handler = _handler(jobs, storage, parser)
    handler.handle(_message())
    handler.close()

    assert parser.paths == []
    assert storage.stores == 0
    assert storage.retrieve(RESULT_PATH) == invalid
    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="internal_error")


def test_different_valid_artifact_is_reused_without_parsing() -> None:
    jobs = MemoryJobs(_queued())
    storage = MemoryStorage()
    stored = _artifact().model_copy(update={"checksum": "other"}).model_dump_json().encode()
    storage.payloads[RESULT_PATH] = stored
    parser = RecordingParser(_artifact())
    handler = _handler(jobs, storage, parser)
    handler.handle(_message())
    handler.close()

    assert parser.paths == []
    assert storage.stores == 0
    assert storage.retrieve(RESULT_PATH) == stored
    assert jobs.state is not None
    assert jobs.state.status == "parsed"
    assert jobs.state.parse_result_ref == RESULT_PATH


def test_identical_existing_artifact_is_reused() -> None:
    jobs = MemoryJobs(_queued().with_changes(status="parsing"))
    storage = MemoryStorage()
    payload = _artifact().model_dump_json().encode()
    storage.payloads[RESULT_PATH] = payload
    parser = RecordingParser(_artifact())
    handler = _handler(jobs, storage, parser)
    handler.handle(_message())
    handler.close()

    assert storage.stores == 0
    assert jobs.state is not None
    assert jobs.state.status == "parsed"
    assert jobs.state.parse_result_ref == RESULT_PATH


def test_missing_job_state_is_retried() -> None:
    jobs = MemoryJobs(None)
    handler = _handler(jobs, MemoryStorage(), RecordingParser(_artifact()))
    with pytest.raises(JobNotFoundError):
        handler.handle(_message())
    handler.close()


class CorruptJobs(MemoryJobs):
    async def get(self, job_id: UUID) -> JobState | None:
        raise CorruptJobStateError(str(job_id))


def test_corrupt_state_is_dropped_without_retry() -> None:
    handler = _handler(CorruptJobs(None), MemoryStorage(), RecordingParser(_artifact()))
    handler.handle(_message())  # returns instead of raising, so the queue does not retry
    handler.close()


def test_on_failed_tolerates_corrupt_state() -> None:
    handler = _handler(CorruptJobs(None), MemoryStorage(), RecordingParser(_artifact()))
    handler.on_failed(_message(), RuntimeError("late"))
    handler.close()


def test_on_failed_records_timeout() -> None:
    jobs = MemoryJobs(_queued().with_changes(status="parsing"))
    handler = _handler(jobs, MemoryStorage(), RecordingParser(_artifact()))
    handler.on_failed(_message(), JobTimeLimitExceeded())
    handler.close()
    assert jobs.state is not None
    assert jobs.state.error == SafeError(code="timeout")


def test_on_failed_does_not_reopen_a_finished_job() -> None:
    finished = _queued().with_changes(
        status="parsed", parse_result_ref="7c2e1d3b-8e55-4f2b-ad4c-3a9f6b1e7d22"
    )
    jobs = MemoryJobs(finished)
    handler = _handler(jobs, MemoryStorage(), RecordingParser(_artifact()))
    handler.on_failed(_message(), RuntimeError("late"))
    handler.close()
    assert jobs.state == finished


@pytest.mark.parametrize("status", ["parsed", "extracting", "done"])
def test_job_past_the_parse_stage_is_left_alone(status: str) -> None:
    fields: dict[str, object] = {
        "status": status,
        "parse_result_ref": "7c2e1d3b-8e55-4f2b-ad4c-3a9f6b1e7d22",
    }
    if status == "done":
        fields["extract_result_ref"] = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
    later = _queued().with_changes(**fields)
    jobs = MemoryJobs(later)
    parser = RecordingParser(_artifact())
    handler = _handler(jobs, MemoryStorage(), parser)
    handler.handle(_message())
    handler.close()
    assert parser.paths == []
    assert jobs.state == later


def test_malformed_message_with_non_string_job_id_is_ignored() -> None:
    jobs = MemoryJobs(_queued())
    handler = _handler(jobs, MemoryStorage(), RecordingParser(_artifact()))
    handler.on_malformed({"job_id": 12345})  # must not raise
    handler.close()
    assert jobs.state == _queued()


def test_killed_parser_process_is_retried_not_failed_as_invalid_pdf() -> None:
    jobs = MemoryJobs(_queued())
    parser = RecordingParser(_artifact(), error=ParserProcessError("killed by signal 9"))
    handler = _handler(jobs, MemoryStorage(), parser)
    with pytest.raises(ParserProcessError):
        handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.status == "parsing"


def test_records_logged_while_a_job_runs_carry_its_job_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _LoggingParser:
        def parse(self, path: Path) -> ParserArtifact:
            logging.getLogger("bioparser.parser.test").info("parsing")
            return _artifact()

    setup_logging("INFO")
    handler = _handler(
        MemoryJobs(_queued()),
        MemoryStorage(),
        RecordingParser(_artifact()),
        factory=lambda _name: _LoggingParser(),
    )
    handler.handle(_message())
    handler.close()

    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    [parsing] = [r for r in records if r["msg"] == "parsing"]
    assert parsing["job_id"] == str(JOB_ID)


# The handler behind the real queue: delivery, retries and the time limit are Dramatiq's.


def _consume(handler: ParseJobHandler, *, max_retries: int, time_limit_ms: int = 60_000) -> None:
    """Submit the job and run it to the end through a real queue on an in-memory broker."""
    broker = StubBroker(fail_fast_default=False)
    try:
        (time_limit,) = [m for m in broker.middleware if isinstance(m, TimeLimit)]
        time_limit.manager.interval = 0.05  # type: ignore[union-attr]
        queue = DramatiqJobQueue(
            name="parse",
            model=ParseJobMessage,
            broker=broker,
            max_retries=max_retries,
            min_backoff_ms=1,
            time_limit_ms=time_limit_ms,
            worker_timeout_ms=50,
        )
        queue.submit(_message())
        queue.consume(handler, until_empty=True)
    finally:
        handler.close()
        broker.flush_all()
        broker.close()


def test_exhausted_wake_retries_try_once_more_when_the_job_is_parsed() -> None:
    jobs = MemoryJobs(_queued())
    calls: list[UUID] = []

    def _enqueue(job_id: UUID) -> None:
        calls.append(job_id)
        raise RuntimeError("queue down")

    handler = _handler(jobs, MemoryStorage(), RecordingParser(_artifact()), enqueue=_enqueue)
    _consume(handler, max_retries=1)

    assert jobs.state is not None
    assert jobs.state.status == "parsed"
    assert jobs.state.error is None
    # Two deliveries, then on_failed sends the wake-up that those deliveries could not.
    assert calls == [JOB_ID, JOB_ID, JOB_ID]


def test_a_job_runs_from_the_queue_to_parsed() -> None:
    jobs = MemoryJobs(_queued())
    storage = MemoryStorage()
    parser = RecordingParser(_artifact())

    _consume(_handler(jobs, storage, parser), max_retries=3)

    assert jobs.state is not None
    assert jobs.state.status == "parsed"
    assert len(parser.paths) == 1
    assert storage.stores == 1


def test_a_failing_parse_is_retried_then_fails_the_job() -> None:
    jobs = MemoryJobs(_queued())
    parser = RecordingParser(_artifact(), error=RuntimeError("boom"))

    _consume(_handler(jobs, MemoryStorage(), parser, max_claims=3), max_retries=2)

    assert len(parser.paths) == 3
    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="internal_error")


def test_claim_cap_stops_retries_that_the_queue_would_still_allow() -> None:
    """A job started max_claims times is not parsed again, even with retries left."""
    jobs = MemoryJobs(_queued())
    parser = RecordingParser(_artifact(), error=RuntimeError("boom"))

    _consume(_handler(jobs, MemoryStorage(), parser, max_claims=2), max_retries=5)

    assert len(parser.paths) == 2
    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="internal_error")


def test_queue_time_limit_fails_the_job_as_timeout_without_retry() -> None:
    class _HangingParser:
        calls = 0

        def parse(self, path: Path) -> ParserArtifact:
            _HangingParser.calls += 1
            for _ in range(1000):
                time.sleep(0.01)
            raise AssertionError("the time limit never interrupted the parse")

    jobs = MemoryJobs(_queued())
    handler = _handler(
        jobs, MemoryStorage(), RecordingParser(_artifact()), factory=lambda _name: _HangingParser()
    )

    _consume(handler, max_retries=3, time_limit_ms=200)

    assert _HangingParser.calls == 1
    assert jobs.state is not None
    assert jobs.state.status == "failed"
    assert jobs.state.error == SafeError(code="timeout")


def test_second_job_with_the_same_pdf_reuses_the_artifact() -> None:
    storage = MemoryStorage()
    parser = RecordingParser(_artifact())
    first = MemoryJobs(_queued())
    handler = _handler(first, storage, parser)
    handler.handle(_message())
    handler.close()

    other_id = UUID("9d0c6a52-1b7e-4c3f-a2d8-5e6f7a8b9c01")
    second = MemoryJobs(_queued().with_changes(job_id=other_id))
    handler = _handler(second, storage, parser)
    handler.handle(_message(job_id=str(other_id)))
    handler.close()

    assert parser.paths == [PDF_BYTES]  # parsed once
    assert second.state is not None
    assert second.state.status == "parsed"
    assert second.state.parse_result_ref == RESULT_PATH


def test_the_mineru_result_path_has_the_tier_as_its_last_segment() -> None:
    jobs = MemoryJobs(_queued().with_changes(parser="mineru"))
    parser = RecordingParser(_artifact())
    handler = ParseJobHandler(
        jobs=jobs,
        storage=MemoryStorage(),
        parser_factory=lambda _name: parser,
        max_claims=DEFAULT_PARSE_MAX_RETRIES + 1,
        mineru_tier="standard",
    )
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    assert jobs.state.parse_result_ref == f"pdf/{PDF_CHECKSUM}/parsed/mineru/2/standard"


def test_each_backend_gets_its_own_result_path() -> None:
    jobs = MemoryJobs(_queued().with_changes(parser="mineru"))
    storage = MemoryStorage()
    parser = RecordingParser(_artifact())
    handler = _handler(jobs, storage, parser, factory=lambda _name: parser)
    handler.handle(_message())
    handler.close()

    assert jobs.state is not None
    # Without a tier the handler names the default tier's path.
    assert jobs.state.parse_result_ref == parse_result_path(PDF_CHECKSUM, "mineru", "2", "basic")
