"""Store one JSON artifact so a retry can reuse it.

Storage cannot replace an object, so an invalid payload already at the path
is permanent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from bioparser.storage import (
    ArtifactAlreadyExistsError,
    ArtifactMetadata,
    ArtifactNotFoundError,
    ArtifactStorage,
    CreationInfo,
)

from .stage import PermanentJobFailure

LOGGER = logging.getLogger(__name__)


async def _retrieve_or_none(storage: ArtifactStorage, artifact_path: str) -> bytes | None:
    """Return stored bytes, or none when the artifact is missing."""
    try:
        return await storage.retrieve(artifact_path)
    except ArtifactNotFoundError:
        return None


async def store_json_artifact(
    storage: ArtifactStorage,
    *,
    schema_version: str,
    payload: bytes,
    artifact_path: str,
    created_by: str,
    is_valid: Callable[[bytes], bool],
    kind: str,
) -> str:
    metadata = ArtifactMetadata(
        artifact_path=artifact_path,
        media_type="application/json",
        content_schema_version=schema_version,
        size_bytes=len(payload),
        creation_info=CreationInfo(created_by=created_by),
    )
    try:
        await storage.store(payload, metadata)
    except ArtifactAlreadyExistsError as exc:
        existing = await _retrieve_or_none(storage, artifact_path)
        if existing is None or not is_valid(existing):
            LOGGER.error(f"stored {kind} from another delivery is missing or invalid")
            raise PermanentJobFailure("internal_error") from exc
    return artifact_path
