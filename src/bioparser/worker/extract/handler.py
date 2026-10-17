"""Extractor consumer for extract-job messages."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from uuid import NAMESPACE_DNS, UUID, uuid5

from pydantic import ValidationError

from bioparser.extract.extract import (
    ExtractionFailedError,
    ExtractionReport,
    artifact_from_report,
)
from bioparser.extract.schema import EXTRACTION_ARTIFACT_SCHEMA_VERSION, ExtractionArtifact
from bioparser.jobqueue import ExtractJobMessage
from bioparser.jobstate import JobState, JobStateStore, JobStatus, StaleJobStateWriteError
from bioparser.parser.models import ParserArtifact
from bioparser.services.vllm.vllm import VLLMService, VLLMTruncatedError
from bioparser.storage import ArtifactNotFoundError, ArtifactStorage

from ..job import CorruptJobRecord, MissingJobRecord, fetch_job
from ..json_artifact import _retrieve_or_none, store_json_artifact
from ..stage import PermanentJobFailure, StageJobHandler

LOGGER = logging.getLogger(__name__)

_EXTRACT_STAGE: frozenset[JobStatus] = frozenset(("parsed", "extracting"))
_EXTRACTION_ARTIFACT_NAMESPACE = uuid5(NAMESPACE_DNS, "bioparser.extraction-artifact")

Extract = Callable[[ParserArtifact], Awaitable[ExtractionReport]]


class ExtractJobHandler(StageJobHandler[ExtractJobMessage]):
    """Read a parser artifact, run the extractor, and store the result."""

    def __init__(
        self,
        *,
        jobs: JobStateStore,
        storage: ArtifactStorage,
        extract: Extract,
        max_claims: int,
        vllm: VLLMService | None = None,
    ) -> None:
        super().__init__(
            jobs=jobs,
            owned=_EXTRACT_STAGE,
            claim_status="extracting",
            max_claims=max_claims,
        )
        self._storage = storage
        self._extract = extract
        self._vllm = vllm

    def close(self) -> None:
        if self._vllm is not None and not self._loop.is_closed():
            self.run_async(self._vllm.aclose())
        super().close()

    async def run(self, state: JobState) -> None:
        artifact_path = await self._ensure_extraction_artifact(state)
        # `state` is from before the claim. `update` replaces the whole record, so
        # writing `done` from it would drop the claim count just stored.
        match await fetch_job(self._jobs, state.job_id):
            case CorruptJobRecord():
                LOGGER.error("stored job state is unreadable. Not recording 'done'")
            case JobState() as latest if latest.status in _EXTRACT_STAGE:
                try:
                    await self._jobs.update(
                        latest.with_changes(status="done", extract_result_ref=artifact_path)
                    )
                except StaleJobStateWriteError:
                    LOGGER.info(
                        "another delivery moved the job on while it was extracting. "
                        "Not recording 'done'"
                    )
            case MissingJobRecord() | JobState():
                return

    async def _ensure_extraction_artifact(self, state: JobState) -> str:
        artifact_path = _extraction_artifact_path(state.job_id)
        stored = _retrieve_or_none(self._storage, artifact_path)
        if stored is not None and _is_extraction_artifact(stored):
            return artifact_path
        if stored is not None:
            # Storage cannot replace an artifact, so an invalid one is permanent.
            LOGGER.error("stored extraction artifact is invalid and cannot be replaced")
            raise PermanentJobFailure("internal_error")
        parser_artifact = self._load_parser_artifact(state)
        try:
            report = await self._extract(parser_artifact)
        except (ExtractionFailedError, VLLMTruncatedError) as exc:
            LOGGER.error(
                f"extraction failed ({type(exc).__name__}). "
                "Marking the job failed (extraction_failed)"
            )
            raise PermanentJobFailure("extraction_failed") from exc
        payload = artifact_from_report(report).model_dump_json().encode()
        return store_json_artifact(
            self._storage,
            schema_version=EXTRACTION_ARTIFACT_SCHEMA_VERSION,
            payload=payload,
            artifact_path=artifact_path,
            created_by="extractor-worker",
            is_valid=_is_extraction_artifact,
            kind="extraction artifact",
        )

    def _load_parser_artifact(self, state: JobState) -> ParserArtifact:
        ref = state.parse_result_ref
        if ref is None:
            LOGGER.error("job has no parser artifact. Marking the job failed (internal_error)")
            raise PermanentJobFailure("internal_error")
        try:
            raw = self._storage.retrieve(ref)
        except ArtifactNotFoundError as exc:
            LOGGER.error("parser artifact is missing. Marking the job failed (internal_error)")
            raise PermanentJobFailure("internal_error") from exc
        try:
            return ParserArtifact.model_validate_json(raw)
        except ValidationError as exc:
            LOGGER.error("parser artifact is invalid. Marking the job failed (internal_error)")
            raise PermanentJobFailure("internal_error") from exc


def _extraction_artifact_path(job_id: UUID) -> str:
    """UUID5 of the job id, distinct from the parser artifact path."""
    return str(uuid5(_EXTRACTION_ARTIFACT_NAMESPACE, str(job_id)))


def _is_extraction_artifact(payload: bytes) -> bool:
    try:
        ExtractionArtifact.model_validate_json(payload)
    except ValidationError:
        return False
    return True
