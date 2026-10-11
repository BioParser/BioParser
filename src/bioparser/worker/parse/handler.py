"""Parse-only consumer for parse-job messages."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from uuid import NAMESPACE_DNS, UUID, uuid5

from pydantic import ValidationError

from bioparser.jobqueue import JobTimeLimitExceeded, ParseJobMessage
from bioparser.jobstate import JobState, JobStateStore, JobStatus, StaleJobStateWriteError
from bioparser.parser import (
    ParserArtifact,
    ParserBackendUnavailableError,
    ParserTimeoutError,
    PdfParser,
    UnsupportedDocumentError,
)
from bioparser.storage import ArtifactNotFoundError, ArtifactStorage

from ..job import CorruptJobRecord, MissingJobRecord, fetch_job
from ..json_artifact import reusable_json_artifact, store_json_artifact
from ..stage import PermanentJobFailure, StageJobHandler

LOGGER = logging.getLogger(__name__)

_PARSE_STAGE: frozenset[JobStatus] = frozenset(("queued", "parsing"))
_PARSER_ARTIFACT_NAMESPACE = uuid5(NAMESPACE_DNS, "bioparser.parser-artifact")


class ParseJobHandler(StageJobHandler[ParseJobMessage]):
    """Fetch a PDF, parse it, store the artifact, and advance job state."""

    def __init__(
        self,
        *,
        jobs: JobStateStore,
        storage: ArtifactStorage,
        parser_factory: Callable[[str], PdfParser],
        max_claims: int,
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
        self._enqueue_extraction = enqueue_extraction

    async def run(self, state: JobState) -> None:
        artifact_path = self._ensure_parser_artifact(state.job_id, state.parser, state.pdf_ref)
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
                    await self._wake_if_parsed(state.job_id)
                    return
                self._wake_extractor(state.job_id)
            case JobState() as latest:
                self.on_outside(latest)
            case MissingJobRecord():
                return

    def on_failed(self, message: ParseJobMessage, exc: BaseException) -> None:
        # The last delivery already failed. A job left at `parsed` cannot be
        # marked failed, so this is the last chance to send the extract wake-up.
        try:
            self.run_async(self._wake_if_parsed(message.job_id))
        finally:
            super().on_failed(message, exc)

    def on_outside(self, state: JobState) -> None:
        if state.status == "parsed":
            self._wake_extractor(state.job_id)

    async def _wake_if_parsed(self, job_id: UUID) -> None:
        match await fetch_job(self._jobs, job_id):
            case JobState() as current:
                self.on_outside(current)
            case CorruptJobRecord() | MissingJobRecord():
                return

    def _wake_extractor(self, job_id: UUID) -> None:
        if self._enqueue_extraction is None:
            return
        self._enqueue_extraction(job_id)

    def _ensure_parser_artifact(self, job_id: UUID, parser_name: str, pdf_ref: str) -> str:
        artifact_path = _parser_artifact_path(job_id)
        if (
            reusable_json_artifact(
                self._storage, artifact_path, _is_parser_artifact, "parser artifact"
            )
            is not None
        ):
            return artifact_path
        artifact = self._parse(parser_name, pdf_ref)
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

    def _parse(self, parser_name: str, pdf_ref: str) -> ParserArtifact:
        """Fetch the input PDF and parse it. Known failures become `PermanentJobFailure`."""
        try:
            parser = self._parser_factory(parser_name)
        except ParserBackendUnavailableError as exc:
            LOGGER.error(f"parser backend {parser_name!r} cannot run: {exc}")
            raise PermanentJobFailure("parse_failed") from exc
        try:
            pdf = self._storage.retrieve(pdf_ref)
        except ArtifactNotFoundError as exc:
            LOGGER.error("input PDF is not in artifact storage")
            raise PermanentJobFailure("internal_error") from exc
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
