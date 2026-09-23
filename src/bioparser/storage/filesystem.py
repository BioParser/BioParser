from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from bioparser.storage.errors import (
    ArtifactAlreadyExistsError,
    ArtifactNotFoundError,
    InvalidArtifactIdError,
    StorageConfigurationError,
    StorageError,
    StoragePathTraversalError,
    StoragePayloadError,
)
from bioparser.storage.models import (
    ArtifactMetadata,
    StoredArtifact,
)


class FileSystemArtifactStorage:
    """Local filesystem implementation of the ArtifactStorage interface.

    Stores artifacts within a configurable root directory as flat files:
      - Payload content: `{artifact_id}.data`
      - Metadata JSON:  `{artifact_id}.meta.json`

    All operations validate that generated paths remain strictly within the storage root.
    """

    _ARTIFACT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
    MAX_ARTIFACT_ID_LENGTH = 128

    def __init__(self, root_path: Path | str, *, create_root: bool = True) -> None:
        self.root_path = Path(root_path).resolve()
        if create_root:
            self.root_path.mkdir(parents=True, exist_ok=True)
        elif not self.root_path.is_dir():
            raise StorageConfigurationError(
                f"Configured storage root directory does not exist: {self.root_path}"
            )

    def _validate_artifact_id(self, artifact_id: str) -> None:
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            raise InvalidArtifactIdError("Artifact ID cannot be empty or whitespace")
        if len(artifact_id) > self.MAX_ARTIFACT_ID_LENGTH:
            raise InvalidArtifactIdError(
                f"Artifact ID exceeds maximum length of {self.MAX_ARTIFACT_ID_LENGTH} characters: {artifact_id}"
            )
        if artifact_id in (".", ".."):
            raise StoragePathTraversalError("Artifact ID cannot be '.' or '..'")
        if not self._ARTIFACT_ID_PATTERN.fullmatch(artifact_id):
            raise InvalidArtifactIdError(
                f"Artifact ID '{artifact_id}' contains invalid characters. "
                "Only alphanumeric characters, '.', '_', and '-' are allowed."
            )

    def _get_artifact_paths(self, artifact_id: str) -> tuple[Path, Path]:
        self._validate_artifact_id(artifact_id)
        content_path = (self.root_path / f"{artifact_id}.data").resolve()
        metadata_path = (self.root_path / f"{artifact_id}.meta.json").resolve()

        if (
            not content_path.is_relative_to(self.root_path)
            or not metadata_path.is_relative_to(self.root_path)
            or content_path == self.root_path
        ):
            raise StoragePathTraversalError(
                f"Generated artifact path escapes storage root: {artifact_id}"
            )
        return content_path, metadata_path

    def store(
        self,
        content: bytes,
        metadata: ArtifactMetadata,
        *,
        overwrite: bool = False,
    ) -> str:
        aid = metadata.artifact_id
        content_path, metadata_path = self._get_artifact_paths(aid)

        if not overwrite and self.exists(aid):
            raise ArtifactAlreadyExistsError(f"Artifact already exists: {aid}")

        if metadata.size_bytes is None:
            metadata = metadata.model_copy(update={"size_bytes": len(content)})
        elif metadata.size_bytes != len(content):
            raise StoragePayloadError(
                f"Metadata size_bytes ({metadata.size_bytes}) does not match "
                f"content length ({len(content)})"
            )

        lock_path = self.root_path / f".lock_{aid}"
        lock_acquired = False

        if not overwrite:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                lock_acquired = True
            except FileExistsError as exc:
                raise ArtifactAlreadyExistsError(f"Artifact already exists: {aid}") from exc
            except OSError as exc:
                raise StorageError(
                    f"Failed to acquire storage reservation for {aid}: {exc}"
                ) from exc

        tmp_suffix = f".tmp_{aid}_{uuid.uuid4().hex}"
        tmp_content = self.root_path / f"{tmp_suffix}.data"
        tmp_metadata = self.root_path / f"{tmp_suffix}.meta.json"

        try:
            tmp_content.write_bytes(content)
            tmp_metadata.write_text(metadata.model_dump_json(indent=2), encoding="utf-8")

            # Write content first, then metadata acts as the commit point
            os.replace(tmp_content, content_path)
            os.replace(tmp_metadata, metadata_path)
        except OSError as exc:
            raise StorageError(f"Failed to write artifact {aid}: {exc}") from exc
        finally:
            if tmp_content.exists():
                tmp_content.unlink(missing_ok=True)
            if tmp_metadata.exists():
                tmp_metadata.unlink(missing_ok=True)
            if lock_acquired and lock_path.exists():
                lock_path.unlink(missing_ok=True)

        return aid

    def retrieve(self, artifact_id: str) -> bytes:
        content_path, _ = self._get_artifact_paths(artifact_id)
        if not self.exists(artifact_id):
            raise ArtifactNotFoundError(f"Artifact content not found: {artifact_id}")

        return content_path.read_bytes()

    def retrieve_metadata(self, artifact_id: str) -> ArtifactMetadata:
        _, metadata_path = self._get_artifact_paths(artifact_id)
        if not self.exists(artifact_id):
            raise ArtifactNotFoundError(f"Artifact metadata not found: {artifact_id}")

        raw_json = metadata_path.read_text(encoding="utf-8")
        return ArtifactMetadata.model_validate_json(raw_json)

    def retrieve_artifact(self, artifact_id: str) -> StoredArtifact:
        content = self.retrieve(artifact_id)
        metadata = self.retrieve_metadata(artifact_id)
        return StoredArtifact(content=content, metadata=metadata)

    def exists(self, artifact_id: str) -> bool:
        content_path, metadata_path = self._get_artifact_paths(artifact_id)
        return content_path.is_file() and metadata_path.is_file()
