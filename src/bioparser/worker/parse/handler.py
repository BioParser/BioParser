"""Parse-only consumer for parse-job messages."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from bioparser.artifact_paths import parse_result_path
from bioparser.hashing import sha256_bytes
from bioparser.jobqueue import JobTimeLimitExceeded, ParseJobMessage
from bioparser.jobstate import JobState, JobStateStore, JobStatus, StaleJobStateWriteError
from bioparser.parser import (
    ParserArtifact,
    ParserBackendUnavailableError,
    ParserTimeoutError,
    PdfParser,
    UnsupportedDocumentError,
)
from bioparser.parser_names import MINERU_PARSER_NAME, PARSER_VERSIONS
from bioparser.services.mineru.client import DEFAULT_TIER, MinerUTier
from bioparser.storage import ArtifactNotFoundError, ArtifactStorage

from ..job import CorruptJobRecord, MissingJobRecord, fetch_job
from ..json_artifact import _retrieve_or_none, store_json_artifact
from ..stage import PermanentJobFailure, StageJobHandler

LOGGER = logging.getLogger(__name__)

_PARSE_STAGE: frozenset[JobStatus] = frozenset(("queued", "parsing"))


class ParseJobHandler(StageJobHandler[ParseJobMessage]):
    """Fetch a PDF, parse it, store the artifact, and advance job state."""

    def __init__(
        self,
        *,
        jobs: JobStateStore,
        storage: ArtifactStorage,
        parser_factory: Callable[[str], PdfParser],
        max_claims: int,
        mineru_tier: MinerUTier = DEFAULT_TIER,
        enqueue_extraction: Callable[[UUID], None] | None = None,
    ) -> None:
        super().__init__(
            jobs=jobs,
            owned=_PARSE_STAGE,
            claim_status="parsing",
            max_claims=max_claims,
        )
        self._storage = storage
        self._parser_factory = parser_factory
        # The tier the worker asks MinerU for. MinerU results are stored per tier.
        self._mineru_tier: MinerUTier = mineru_tier
        self._enqueue_extraction = enqueue_extraction

    def handle(self, message: ParseJobMessage) -> None:
        """After the delivery, send the extract message when the job is `parsed`.

        A failed send fails this delivery, so the queue tries it again. A failed
        read does not replace the delivery's own error.
        """
        try:
            super().handle(message)
        except BaseException:
            self._wake_without_hiding_the_delivery_error(message.job_id)
            raise
        else:
            self._wake_extractor_if_parsed(message.job_id)

    async def run(self, state: JobState) -> None:
        artifact_path = self._ensure_parser_artifact(state.parser, state.pdf_ref)
        # `state` is from before the claim. `update` replaces the whole record, so
        # writing `parsed` from it would drop the claim count just stored.
        match await fetch_job(self._jobs, state.job_id):
            case CorruptJobRecord():
                LOGGER.error("stored job state is unreadable. Not recording 'parsed'")
            case JobState() as latest if latest.status in _PARSE_STAGE:
                try:
                    updated = latest.with_changes(status="parsed", parse_result_ref=artifact_path)
                    await self._jobs.update(updated)
                except StaleJobStateWriteError:
                    LOGGER.info(
                        "another delivery moved the job on while it was parsing. "
                        "Not recording 'parsed'"
                    )
            case MissingJobRecord() | JobState():
                return

    def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
        # The last delivery already failed. A job left at `parsed` cannot be
        # marked failed, so this is the last chance to send the extract wake-up.
        try:
            self._wake_extractor_if_parsed(message.job_id)
        finally:
            super().on_failed(message, exc)

    def _wake_without_hiding_the_delivery_error(self, job_id: UUID) -> None:
        try:
            self._wake_extractor_if_parsed(job_id)
        except Exception as exc:  # noqa: BLE001 - the wake must not replace the delivery's error
            LOGGER.error(
                f"extract message was not sent ({type(exc).__name__}). The delivery's error stands"
            )

    def _wake_extractor_if_parsed(self, job_id: UUID) -> None:
        if self._enqueue_extraction is None:
            return
        if self.run_async(self._job_is_parsed(job_id)):
            self._enqueue_extraction(job_id)

    async def _job_is_parsed(self, job_id: UUID) -> bool:
        match await fetch_job(self._jobs, job_id):
            case JobState(status="parsed"):
                return True
            case JobState() | CorruptJobRecord() | MissingJobRecord():
                return False

    def _ensure_parser_artifact(self, parser_name: str, pdf_ref: str) -> str:
        """Return the artifact path for the parsed PDF, creating it if needed."""
        pdf = self._retrieve_pdf(pdf_ref)
        artifact_path = _parse_result_path(pdf, parser_name, self._mineru_tier)
        stored = _retrieve_or_none(self._storage, artifact_path)
        if stored is not None and _is_parser_artifact(stored):
            return artifact_path
        if stored is not None:
            # Storage cannot replace an artifact, so an invalid one is permanent.
            LOGGER.error("stored parser artifact is invalid and cannot be replaced")
            raise PermanentJobFailure("internal_error")
        artifact = self._parse(parser_name, pdf)
        payload = artifact.model_dump_json().encode()
        return store_json_artifact(
            self._storage,
            schema_version=artifact.schema_version,
            payload=payload,
            artifact_path=artifact_path,
            created_by="parser-worker",
            is_valid=_is_parser_artifact,
            kind="parser artifact",
        )

    def _retrieve_pdf(self, pdf_ref: str) -> bytes:
        try:
            return self._storage.retrieve(pdf_ref)
        except ArtifactNotFoundError as exc:
            LOGGER.error("input PDF is not in artifact storage")
            raise PermanentJobFailure("internal_error") from exc

    def _parse(self, parser_name: str, pdf: bytes) -> ParserArtifact:
        """Parse the input PDF. Known failures become `PermanentJobFailure`."""
        try:
            parser = self._parser_factory(parser_name)
        except ParserBackendUnavailableError as exc:
            LOGGER.error(f"parser backend {parser_name!r} cannot run: {exc}")
            raise PermanentJobFailure("parse_failed") from exc
        try:
            return _parse_pdf(parser, pdf)
        except ParserBackendUnavailableError as exc:
            LOGGER.error(f"parser backend {parser_name!r} cannot run: {exc}")
            raise PermanentJobFailure("parse_failed") from exc
        except (ParserTimeoutError, JobTimeLimitExceeded) as exc:
            # The parser's own limit or the queue's. The same parse would run out of time
            # again, so it is not retried.
            LOGGER.error(f"parser {parser_name!r} timed out: {type(exc).__name__}")
            raise PermanentJobFailure("timeout") from exc
        except UnsupportedDocumentError as exc:
            LOGGER.error(f"parser {parser_name!r} could not read the PDF: {type(exc).__name__}")
            raise PermanentJobFailure("invalid_pdf") from exc


def _parse_result_path(pdf: bytes, parser_name: str, mineru_tier: MinerUTier) -> str:
    """The conventional path for this PDF's parser artifact.

    The same PDF and parser always give the same path, so a retry or another job with the
    same PDF reuses the stored artifact. MinerU's output also depends on its tier, so each
    tier has its own path.
    """
    try:
        version = PARSER_VERSIONS[parser_name]
    except KeyError as exc:
        LOGGER.error(f"parser backend {parser_name!r} has no version")
        raise PermanentJobFailure("internal_error") from exc
    configuration = mineru_tier if parser_name == MINERU_PARSER_NAME else None
    return parse_result_path(sha256_bytes(pdf), parser_name, version, configuration)


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
