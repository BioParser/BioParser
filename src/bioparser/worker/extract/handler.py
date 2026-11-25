"""Extractor consumer for extract-job messages."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from bioparser.artifact_paths import ArtifactPathError, extract_result_path
from bioparser.db import engine as database
from bioparser.db import jobs as job_queries
from bioparser.extract.checks import checked_artifact
from bioparser.extract.errors import ExtractionFailedError, ExtractorBackendUnavailableError
from bioparser.extract.models import EXTRACTION_ARTIFACT_SCHEMA_VERSION, ExtractionArtifact
from bioparser.extract.protocol import Extractor
from bioparser.extractor_names import EXTRACTOR_VERSIONS
from bioparser.jobs import JobState, JobStatus
from bioparser.jobs.queue import ExtractJobMessage
from bioparser.parser.models import ParserArtifact
from bioparser.services.vllm.vllm import VLLMService
from bioparser.storage import ArtifactNotFoundError, ArtifactStorage

from ..json_artifact import _retrieve_or_none, store_json_artifact
from ..stage import PermanentJobFailure, StageJobHandler

LOGGER = logging.getLogger(__name__)

_EXTRACT_STAGE: frozenset[JobStatus] = frozenset(("parsed", "extracting"))


class ExtractJobHandler(StageJobHandler[ExtractJobMessage]):
    """Read a parser artifact, run the extractor, and store the result."""

    def __init__(
        self,
        *,
        database_url: str,
        storage: ArtifactStorage,
        extractor_factory: Callable[[str], Extractor],
        max_claims: int,
        vllm: VLLMService | None = None,
        before_claim: Callable[[JobState], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(
            database_url=database_url,
            owned=_EXTRACT_STAGE,
            claim_status="extracting",
            max_claims=max_claims,
            before_claim=before_claim,
        )
        self._storage = storage
        self._extractor_factory = extractor_factory
        self._vllm = vllm

    async def aclose(self) -> None:
        try:
            if self._vllm is not None:
                await self._vllm.aclose()
        finally:
            await super().aclose()

    async def run(self, state: JobState) -> None:
        artifact_path = await self._ensure_extraction_artifact(state)
        async with database.transaction() as connection:
            wrote = await job_queries.record_done(connection, state.job_id, artifact_path)
        if not wrote:
            LOGGER.info(
                "another delivery moved the job on while it was extracting. Not recording 'done'"
            )

    async def _ensure_extraction_artifact(self, state: JobState) -> str:
        ref = state.parse_result_ref
        if ref is None:
            LOGGER.error("job has no parser artifact. Marking the job failed (internal_error)")
            raise PermanentJobFailure("internal_error")
        expected_version = EXTRACTOR_VERSIONS.get(state.extractor)
        if expected_version is None:
            LOGGER.error(f"extractor {state.extractor!r} has no version")
            raise PermanentJobFailure("internal_error")
        extractor = self._build_extractor(state.extractor)
        artifact_path = _extraction_artifact_path(ref, extractor, state)
        parser_artifact = await self._load_parser_artifact(ref)
        stored = await _retrieve_or_none(self._storage, artifact_path)
        if stored is not None and _is_extraction_artifact(stored):
            return artifact_path
        if stored is not None:
            # Storage cannot replace an artifact, so an invalid one is permanent.
            LOGGER.error("stored extraction artifact is invalid and cannot be replaced")
            raise PermanentJobFailure("internal_error")
        try:
            artifact = checked_artifact(
                await extractor.extract(parser_artifact),
                parser_artifact,
                extractor=state.extractor,
                version=expected_version,
            )
        except ExtractorBackendUnavailableError as exc:
            LOGGER.error(f"extractor {state.extractor!r} cannot run: {exc}")
            raise PermanentJobFailure("internal_error") from exc
        except ExtractionFailedError as exc:
            cause = exc.__cause__ or exc
            LOGGER.error(
                f"extraction failed ({type(cause).__name__}: {cause}). "
                "Marking the job failed (extraction_failed)"
            )
            raise PermanentJobFailure("extraction_failed") from exc
        payload = artifact.model_dump_json().encode()
        return await store_json_artifact(
            self._storage,
            schema_version=EXTRACTION_ARTIFACT_SCHEMA_VERSION,
            payload=payload,
            artifact_path=artifact_path,
            created_by="extractor-worker",
            is_valid=_is_extraction_artifact,
            kind="extraction artifact",
        )

    def _build_extractor(self, extractor_name: str) -> Extractor:
        try:
            return self._extractor_factory(extractor_name)
        except ExtractorBackendUnavailableError as exc:
            LOGGER.error(f"extractor {extractor_name!r} cannot run: {exc}")
            raise PermanentJobFailure("internal_error") from exc

    async def _load_parser_artifact(self, ref: str) -> ParserArtifact:
        try:
            raw = await self._storage.retrieve(ref)
        except ArtifactNotFoundError as exc:
            LOGGER.error("parser artifact is missing. Marking the job failed (internal_error)")
            raise PermanentJobFailure("internal_error") from exc
        try:
            return ParserArtifact.model_validate_json(raw)
        except ValidationError as exc:
            LOGGER.error("parser artifact is invalid. Marking the job failed (internal_error)")
            raise PermanentJobFailure("internal_error") from exc


def _extraction_artifact_path(parse_result_ref: str, extractor: Extractor, state: JobState) -> str:
    """The conventional path for this parser artifact's extraction result.

    The path starts from the parser artifact's own path followed by the extractor-defined
    segments for the job state, so two parses of one PDF (such as two MinerU tiers) or
    distinct extractor configurations/jobs do not share a result when their segments differ.
    """
    try:
        segments = extractor.artifact_path_segments(state)
        return extract_result_path(parse_result_ref, segments)
    except (ArtifactPathError, KeyError) as exc:
        LOGGER.error(f"extractor cannot form an artifact path: {exc}")
        raise PermanentJobFailure("internal_error") from exc


def _is_extraction_artifact(payload: bytes) -> bool:
    try:
        ExtractionArtifact.model_validate_json(payload)
    except ValidationError:
        return False
    return True
