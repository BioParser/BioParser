"""Extractor consumer for extract-job messages."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from bioparser.extract.extract import (
    ExtractionFailedError,
    ExtractionReport,
    artifact_from_report,
)
from bioparser.extract.schema import EXTRACTION_ARTIFACT_SCHEMA_VERSION, ExtractionArtifact
from bioparser.artifact_paths import ArtifactPathError, extract_result_path
from bioparser.jobqueue import ExtractJobMessage, JobDeferred
from bioparser.jobstate import JobState, JobStateStore, JobStatus, StaleJobStateWriteError
from bioparser.parser.models import ParserArtifact
from bioparser.services.vllm.vllm import (
    VLLMRequestRejectedError,
    VLLMService,
    VLLMTruncatedError,
)
from bioparser.storage import ArtifactNotFoundError, ArtifactStorage

from ..job import CorruptJobRecord, MissingJobRecord, fetch_job
from ..json_artifact import _retrieve_or_none, store_json_artifact
from ..stage import PermanentJobFailure, StageJobHandler

LOGGER = logging.getLogger(__name__)

_EXTRACT_STAGE: frozenset[JobStatus] = frozenset(("parsed", "extracting"))

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

    async def ready(self) -> None:
        if self._vllm is None or await self._vllm.ping():
            return
        LOGGER.warning("vLLM is not answering. Sending the job again later")
        raise JobDeferred("vLLM is not answering")

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
        parser_artifact = self._load_parser_artifact(state)
        artifact_path = _extraction_artifact_path(parser_artifact)
        stored = _retrieve_or_none(self._storage, artifact_path)
        if stored is not None and _is_extraction_artifact(stored):
            return artifact_path
        if stored is not None:
            # Storage cannot replace an artifact, so an invalid one is permanent.
            LOGGER.error("stored extraction artifact is invalid and cannot be replaced")
            raise PermanentJobFailure("internal_error")
        try:
            report = await self._extract(parser_artifact)
        except (ExtractionFailedError, VLLMTruncatedError, VLLMRequestRejectedError) as exc:
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


def _extraction_artifact_path(artifact: ParserArtifact) -> str:
    """The conventional path for this parser artifact's extraction result.

    The same PDF, parser, and extractor always give the same path, so a retry or another
    job with the same parser artifact reuses the stored extraction. One extractor exists,
    so its name and version are fixed here until a job can choose them. Version ``0`` is
    that placeholder: a later ``default`` extractor starts at ``1`` and does not reuse these files.
    """
    try:
        return extract_result_path(
            artifact.checksum,
            artifact.parser.name,
            artifact.parser.version,
            "default",
            "0",
        )
    except ArtifactPathError as exc:
        LOGGER.error("parser artifact cannot form an extraction path")
        raise PermanentJobFailure("internal_error") from exc


def _is_extraction_artifact(payload: bytes) -> bool:
    try:
        ExtractionArtifact.model_validate_json(payload)
    except ValidationError:
        return False
    return True
