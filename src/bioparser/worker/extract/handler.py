"""Extractor consumer for extract-job messages."""

from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic import ValidationError

from bioparser.artifact_paths import ArtifactPathError, extract_result_path
from bioparser.extract.errors import ExtractorUnavailableError
from bioparser.extract.extract import (
    ExtractionFailedError,
    artifact_from_report,
)
from bioparser.extract.protocol import Extractor
from bioparser.extract.schema import EXTRACTION_ARTIFACT_SCHEMA_VERSION, ExtractionArtifact
from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME, EXTRACTOR_VERSIONS
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


class ExtractJobHandler(StageJobHandler[ExtractJobMessage]):
    """Read a parser artifact, run the extractor, and store the result."""

    def __init__(
        self,
        *,
        jobs: JobStateStore,
        storage: ArtifactStorage,
        extractor_factory: Callable[[str], Extractor],
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
        self._extractor_factory = extractor_factory
        self._vllm = vllm

    async def ready(self) -> None:
        if self._vllm is None or await self._vllm.ping():
            return
        LOGGER.warning("vLLM is not answering")
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
        ref = state.parse_result_ref
        if ref is None:
            LOGGER.error("job has no parser artifact. Marking the job failed (internal_error)")
            raise PermanentJobFailure("internal_error")
        name = DEFAULT_EXTRACTOR_NAME
        artifact_path = _extraction_artifact_path(ref, name)
        parser_artifact = self._load_parser_artifact(ref)
        stored = _retrieve_or_none(self._storage, artifact_path)
        if stored is not None and _is_extraction_artifact(stored):
            return artifact_path
        if stored is not None:
            # Storage cannot replace an artifact, so an invalid one is permanent.
            LOGGER.error("stored extraction artifact is invalid and cannot be replaced")
            raise PermanentJobFailure("internal_error")
        try:
            extractor = self._extractor_factory(name)
        except ExtractorUnavailableError as exc:
            LOGGER.error(f"extractor {name!r} cannot run: {exc}")
            raise PermanentJobFailure("internal_error") from exc
        try:
            report = await extractor.extract(parser_artifact)
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

    def _load_parser_artifact(self, ref: str) -> ParserArtifact:
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


def _extraction_artifact_path(parse_result_ref: str, extractor: str) -> str:
    """The conventional path for this parser artifact's extraction result.

    The path is the parser artifact's own path plus the extractor, so two parses of one
    PDF, such as two MinerU tiers, do not share a result. The version is whatever
    ``EXTRACTOR_VERSIONS`` says for this extractor when the job runs. Version ``0`` is the
    placeholder for ``default``: the real default extractor starts at ``1`` and does not
    reuse these files.
    """
    try:
        version = EXTRACTOR_VERSIONS[extractor]
    except KeyError as exc:
        LOGGER.error(f"extractor {extractor!r} has no version")
        raise PermanentJobFailure("internal_error") from exc
    try:
        return extract_result_path(parse_result_ref, extractor, version)
    except ArtifactPathError as exc:
        LOGGER.error("parser artifact path cannot form an extraction path")
        raise PermanentJobFailure("internal_error") from exc


def _is_extraction_artifact(payload: bytes) -> bool:
    try:
        ExtractionArtifact.model_validate_json(payload)
    except ValidationError:
        return False
    return True
