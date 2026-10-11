"""Parse-only consumer for parse-job messages.

The queue handler is synchronous. Job state is async, so this process keeps one
event loop and runs each state call to completion on it. Parsing itself stays
on the calling thread.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import NAMESPACE_DNS, UUID, uuid5

from pydantic import ValidationError

from bioparser.jobqueue import JobHandler, JobTimeLimitExceeded, ParseJobMessage
from bioparser.jobstate import (
    CorruptJobStateError,
    JobNotFoundError,
    JobState,
    JobStateStore,
    JobStatus,
    SafeError,
    SafeErrorCode,
    StaleJobStateWriteError,
)
from bioparser.logging_config import log_context
from bioparser.parser import (
    ParserArtifact,
    ParserBackendUnavailableError,
    ParserTimeoutError,
    PdfParser,
    UnsupportedDocumentError,
)
from bioparser.storage import (
    ArtifactAlreadyExistsError,
    ArtifactMetadata,
    ArtifactNotFoundError,
    ArtifactStorage,
    CreationInfo,
)

LOGGER = logging.getLogger(__name__)

_PARSE_STAGE: frozenset[JobStatus] = frozenset(("queued", "parsing"))
_PARSER_ARTIFACT_NAMESPACE = uuid5(NAMESPACE_DNS, "bioparser.parser-artifact")


class _PermanentJobFailure(Exception):
    """A job error that must be recorded and not retried."""

    def __init__(self, code: SafeErrorCode) -> None:
        self.code = code
        super().__init__(code)


class ParseJobHandler(JobHandler[ParseJobMessage]):
    """Fetch a PDF, parse it, store the artifact, and advance job state."""

    def __init__(
        self,
        *,
        jobs: JobStateStore,
        storage: ArtifactStorage,
        parser_factory: Callable[[str], PdfParser],
        max_claims: int,
    ) -> None:
        self._jobs = jobs
        self._storage = storage
        self._parser_factory = parser_factory
        self._max_claims = max_claims
        self._loop = asyncio.new_event_loop()
        # One loop serves every callback, so calls from different threads take turns.
        self._loop_lock = threading.Lock()

    def close(self) -> None:
        if self._loop.is_closed():
            return
        try:
            self.run_async(self._jobs.aclose())
        finally:
            self._loop.close()

    def run_async[T](self, awaitable: Awaitable[T]) -> T:
        """Run one awaitable to completion on the shared loop.

        The queue can raise JobTimeLimitExceeded in this thread at any point,
        including while the loop is running. The interrupted task would stay
        on the loop and resume during the next call, so it is cancelled and
        drained before the exception leaves.
        """
        with self._loop_lock:
            task = asyncio.ensure_future(awaitable, loop=self._loop)
            try:
                return self._loop.run_until_complete(task)
            except BaseException:
                task.cancel()
                self._loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
                raise

    def handle(self, message: ParseJobMessage) -> None:
        with log_context(job_id=str(message.job_id)):
            self.run_async(self._handle(message.job_id))

    def on_malformed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        try:
            job_id = UUID(payload["job_id"])
        except (KeyError, TypeError, ValueError, AttributeError):
            # UUID() raises AttributeError, not TypeError, for a non-string such as an int.
            return
        with log_context(job_id=str(job_id)):
            LOGGER.error(
                "queue message is malformed and can never be processed. Marking the job failed (internal_error)"
            )
            self.run_async(self._mark_failed(job_id, "internal_error"))

    def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
        code: SafeErrorCode = (
            "timeout" if isinstance(exc, JobTimeLimitExceeded) else "internal_error"
        )
        with log_context(job_id=str(message.job_id)):
            LOGGER.error(f"giving up after {type(exc).__name__}. Marking the job failed ({code})")
            self.run_async(self._mark_failed(message.job_id, code))

    async def _handle(self, job_id: UUID) -> None:
        try:
            state = await self._jobs.get(job_id)
        except CorruptJobStateError:
            # Retrying reads the same bytes. The record cannot be rewritten without its fields.
            LOGGER.error(
                "stored job state is unreadable. Dropping the message without retry, the job stays in its stored status"
            )
            return
        if state is None:
            raise JobNotFoundError(f"no stored state for job {job_id}. Message will be retried")
        if _left_parse_stage(state.status):
            LOGGER.info(f"status is already {state.status!r}. Skipping this duplicate delivery")
            return
        if not await self._mark_running(state):
            return
        try:
            artifact_path = self._ensure_parser_artifact(job_id, state.parser, state.pdf_ref)
        except _PermanentJobFailure as exc:
            LOGGER.error(f"parsing failed. Marking the job failed ({exc.code})")
            await self._mark_failed(job_id, exc.code)
            return
        try:
            latest = await self._jobs.get(job_id)
        except CorruptJobStateError:
            LOGGER.error("stored job state is unreadable. Not recording 'parsed'")
            return
        if latest is None or _left_parse_stage(latest.status):
            LOGGER.info("job moved on while it was parsing. Not recording 'parsed'")
            return
        try:
            # The claim is already stored. This read picks it up, so the full write keeps the count.
            await self._jobs.update(
                latest.with_changes(status="parsed", parse_result_ref=artifact_path)
            )
        except StaleJobStateWriteError:
            LOGGER.info(
                "another delivery or a later stage moved the job on while it was parsing. Not recording 'parsed'"
            )

    async def _mark_running(self, state: JobState) -> bool:
        if state.claims("parsing") >= self._max_claims:
            LOGGER.error(
                f"parsing was already claimed {state.claims('parsing')} times. "
                "Marking the job failed (internal_error)"
            )
            await self._mark_failed(state.job_id, "internal_error")
            return False
        try:
            await self._jobs.update(state.with_claim("parsing"))
        except StaleJobStateWriteError:
            LOGGER.info(
                "job moved past the parse stage before this delivery could start parsing. Skipping"
            )
            return False
        return True

    async def _mark_failed(self, job_id: UUID, code: SafeErrorCode) -> None:
        try:
            state = await self._jobs.get(job_id)
        except CorruptJobStateError:
            LOGGER.error("stored job state is unreadable. Cannot mark it failed")
            return
        if state is None:
            LOGGER.error("no stored job state exists. Cannot mark it failed")
            return
        if _left_parse_stage(state.status):
            return
        try:
            await self._jobs.update(state.with_changes(status="failed", error=SafeError(code=code)))
        except StaleJobStateWriteError:
            LOGGER.info(f"job already finished or moved on. Not marking it failed ({code})")

    def _ensure_parser_artifact(self, job_id: UUID, parser_name: str, pdf_ref: str) -> str:
        artifact_path = _parser_artifact_path(job_id)
        stored = _retrieve_or_none(self._storage, artifact_path)
        if stored is not None and _is_parser_artifact(stored):
            return artifact_path
        if stored is not None:
            # The storage interface cannot replace an artifact, so an invalid one is permanent.
            LOGGER.error(
                f"stored parser artifact {artifact_path} is invalid and cannot be replaced through the storage interface"
            )
            raise _PermanentJobFailure("internal_error")
        artifact = self._parse(job_id, parser_name, pdf_ref)
        payload = artifact.model_dump_json().encode()
        return self._store_artifact(job_id, artifact.schema_version, payload, artifact_path)

    def _parse(self, job_id: UUID, parser_name: str, pdf_ref: str) -> ParserArtifact:
        """Fetch the input PDF and parse it. Known failures become `_PermanentJobFailure`."""
        try:
            parser = self._parser_factory(parser_name)
        except ParserBackendUnavailableError as exc:
            LOGGER.error(f"parser backend {parser_name!r} cannot run: {exc}")
            raise _PermanentJobFailure("parse_failed") from exc
        try:
            pdf = self._storage.retrieve(pdf_ref)
        except ArtifactNotFoundError as exc:
            LOGGER.error(f"input PDF {pdf_ref} not found in artifact storage")
            raise _PermanentJobFailure("internal_error") from exc
        try:
            return _parse_pdf(parser, pdf)
        except ParserBackendUnavailableError as exc:
            LOGGER.error(f"parser backend {parser_name!r} cannot run: {exc}")
            raise _PermanentJobFailure("parse_failed") from exc
        except (ParserTimeoutError, JobTimeLimitExceeded) as exc:
            # The parser's own limit or the queue's. The same parse would run out of time
            # again, so it is not retried.
            LOGGER.error(f"parser {parser_name!r} timed out: {exc!r}")
            raise _PermanentJobFailure("timeout") from exc
        except UnsupportedDocumentError as exc:
            LOGGER.error(f"parser {parser_name!r} could not read the PDF: {exc}")
            raise _PermanentJobFailure("invalid_pdf") from exc

    def _store_artifact(
        self,
        job_id: UUID,
        schema_version: str,
        payload: bytes,
        artifact_path: str,
    ) -> str:
        metadata = ArtifactMetadata(
            artifact_path=artifact_path,
            media_type="application/json",
            content_schema_version=schema_version,
            size_bytes=len(payload),
            creation_info=CreationInfo(created_by="parser-worker"),
        )
        try:
            self._storage.store(payload, metadata)
        except ArtifactAlreadyExistsError as exc:
            # Another delivery stored it first. Keep its artifact if it is valid.
            existing = _retrieve_or_none(self._storage, artifact_path)
            if existing is None:
                LOGGER.error(
                    f"storage said parser artifact {artifact_path} already exists, but it is gone when read back"
                )
                raise _PermanentJobFailure("internal_error") from exc
            if not _is_parser_artifact(existing):
                LOGGER.error(
                    f"parser artifact {artifact_path} written by another delivery is invalid and cannot be replaced"
                )
                raise _PermanentJobFailure("internal_error") from exc
        return artifact_path


def _left_parse_stage(status: JobStatus) -> bool:
    """True once the job has left the parse stage, however it left."""
    return status not in _PARSE_STAGE


def _parser_artifact_path(job_id: UUID) -> str:
    """UUID5 of the job id as the artifact path, so a retry stores the same artifact."""
    return str(uuid5(_PARSER_ARTIFACT_NAMESPACE, str(job_id)))


def _parse_pdf(parser: PdfParser, pdf: bytes) -> ParserArtifact:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(pdf)
        handle.flush()
        return parser.parse(Path(handle.name))


def _is_parser_artifact(payload: bytes) -> bool:
    try:
        ParserArtifact.model_validate_json(payload)
    except ValidationError:
        return False
    return True


def _retrieve_or_none(storage: ArtifactStorage, artifact_path: str) -> bytes | None:
    try:
        return storage.retrieve(artifact_path)
    except ArtifactNotFoundError:
        return None
