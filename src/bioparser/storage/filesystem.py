from __future__ import annotations

import contextlib
import os
import re
import uuid
from pathlib import Path

from pydantic import Field, ValidationError

from bioparser.storage.errors import (
    ArtifactAlreadyExistsError,
    ArtifactNotFoundError,
    InvalidArtifactPathError,
    StorageConfigurationError,
    StorageError,
    StoragePathTraversalError,
    StoragePayloadError,
)
from bioparser.storage.models import (
    ArtifactMetadata,
    StoredArtifact,
)

_artifact_path_PATTERN = re.compile(r"[A-Za-z0-9_./-]+")
_MAX_artifact_path_LENGTH = 512


class FileBlobMetadata(ArtifactMetadata):
    """Storage-local metadata envelope tracking the active filesystem blob."""

    blob_id: str = Field(min_length=1)

    def to_artifact_metadata(self) -> ArtifactMetadata:
        """Convert to public ArtifactMetadata contract without internal storage fields."""
        data = self.model_dump()
        data.pop("blob_id", None)
        return ArtifactMetadata.model_validate(data)


class FileSystemArtifactStorage:
    """Local filesystem implementation of the ArtifactStorage interface.

    Stores artifacts within a configurable root directory using a blob-pointer layout:
      - Payload content: `{artifact_path}.{blob_id}.data`
      - Metadata JSON:   `{artifact_path}.meta.json`

    Payloads are written as immutable blobs first, then the metadata JSON file is
    atomically linked as the single commit point. If the artifact already exists, an error is raised.
    All operations validate that generated paths remain strictly within the storage root.
    """

    def __init__(self, root_path: Path | str, *, create_root: bool = True) -> None:
        self.root_path = Path(root_path).resolve()
        if create_root:
            self.root_path.mkdir(parents=True, exist_ok=True)
        elif not self.root_path.is_dir():
            raise StorageConfigurationError(
                f"Configured storage root directory does not exist: {self.root_path}"
            )

    def _validate_artifact_path(self, artifact_path: str) -> None:
        if not isinstance(artifact_path, str) or not artifact_path.strip():
            raise InvalidArtifactPathError("Artifact path cannot be empty or whitespace")
        if artifact_path.startswith("/"):
            raise InvalidArtifactPathError("Artifact path must not be absolute (no leading '/')")
        if len(artifact_path) > _MAX_artifact_path_LENGTH:
            raise InvalidArtifactPathError(
                f"Artifact path exceeds maximum length of {_MAX_artifact_path_LENGTH} characters: {artifact_path}"
            )
        if any(part in (".", "..") for part in artifact_path.split("/")):
            raise StoragePathTraversalError(
                "Artifact path cannot contain '.' or '..' path segments"
            )
        if any(len(part) > 200 for part in artifact_path.split("/")):
            raise InvalidArtifactPathError(
                "Artifact path contains a segment exceeding 200 characters"
            )
        if not _artifact_path_PATTERN.fullmatch(artifact_path):
            raise InvalidArtifactPathError(
                f"Artifact path '{artifact_path}' contains invalid characters. "
                "Only alphanumeric characters, '/', '.', '_', and '-' are allowed."
            )

    def _get_metadata_path(self, artifact_path: str) -> Path:
        self._validate_artifact_path(artifact_path)
        metadata_path = (self.root_path / f"{artifact_path}.meta.json").resolve()
        if not metadata_path.is_relative_to(self.root_path) or metadata_path == self.root_path:
            raise StoragePathTraversalError(
                f"Generated artifact path escapes storage root: {artifact_path}"
            )
        return metadata_path

    def _get_blob_data_path(self, artifact_path: str, blob_id: str) -> Path:
        self._validate_artifact_path(artifact_path)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", blob_id):
            raise StoragePathTraversalError(f"Invalid blob ID: {blob_id}")
        content_path = (self.root_path / f"{artifact_path}.{blob_id}.data").resolve()
        if not content_path.is_relative_to(self.root_path) or content_path == self.root_path:
            raise StoragePathTraversalError(
                f"Generated artifact path escapes storage root: {artifact_path}"
            )
        return content_path

    def _read_file_blob_metadata(self, artifact_path: str) -> FileBlobMetadata:
        metadata_path = self._get_metadata_path(artifact_path)
        if not metadata_path.is_file():
            raise ArtifactNotFoundError(f"Artifact metadata not found: {artifact_path}")

        try:
            raw_json = metadata_path.read_text(encoding="utf-8")
            return FileBlobMetadata.model_validate_json(raw_json)
        except (ValidationError, ValueError) as exc:
            raise StoragePayloadError(
                f"Corrupted metadata for artifact {artifact_path}: {exc}"
            ) from exc
        except OSError as exc:
            raise StorageError(f"Failed to read metadata for {artifact_path}: {exc}") from exc

    def _read_content(self, artifact_path: str, meta: FileBlobMetadata) -> bytes:
        data_path = self._get_blob_data_path(artifact_path, meta.blob_id)
        if not data_path.is_file():
            raise ArtifactNotFoundError(f"Artifact content not found: {artifact_path}")

        try:
            content = data_path.read_bytes()
        except OSError as exc:
            raise StorageError(f"Failed to read artifact {artifact_path}: {exc}") from exc

        if meta.size_bytes is not None and len(content) != meta.size_bytes:
            raise StoragePayloadError(
                f"Artifact {artifact_path} content size ({len(content)}) does not match "
                f"metadata size_bytes ({meta.size_bytes})"
            )

        return content

    def store(
        self,
        content: bytes,
        metadata: ArtifactMetadata,
    ) -> str:
        aid = metadata.artifact_path

        metadata_path = self._get_metadata_path(aid)

        if metadata.size_bytes is None:
            metadata = metadata.model_copy(update={"size_bytes": len(content)})
        elif metadata.size_bytes != len(content):
            raise StoragePayloadError(
                f"Metadata size_bytes ({metadata.size_bytes}) does not match "
                f"content length ({len(content)})"
            )

        blob_id = uuid.uuid4().hex
        blob_meta = FileBlobMetadata(
            **metadata.model_dump(),
            blob_id=blob_id,
        )

        blob_data_path = self._get_blob_data_path(aid, blob_id)

        # Ensure parent directories exist for both metadata and data
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        blob_data_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_meta_path = metadata_path.parent / f".tmp_{blob_id}.meta.json"

        blob_written = False
        tmp_meta_written = False
        committed = False

        try:
            blob_data_path.write_bytes(content)
            blob_written = True

            tmp_meta_path.write_text(blob_meta.model_dump_json(indent=2), encoding="utf-8")
            tmp_meta_written = True

            try:
                os.link(tmp_meta_path, metadata_path)
                committed = True
            except FileExistsError as exc:
                raise ArtifactAlreadyExistsError(f"Artifact already exists: {aid}") from exc

        except (ArtifactAlreadyExistsError, StoragePayloadError, InvalidArtifactPathError):
            raise
        except OSError as exc:
            raise StorageError(f"Failed to write artifact {aid}: {exc}") from exc
        finally:
            if tmp_meta_written:
                with contextlib.suppress(OSError):
                    tmp_meta_path.unlink(missing_ok=True)
            if not committed and blob_written:
                with contextlib.suppress(OSError):
                    blob_data_path.unlink(missing_ok=True)

        return aid

    def retrieve(self, artifact_path: str) -> bytes:
        record = self._read_file_blob_metadata(artifact_path)
        return self._read_content(artifact_path, record)

    def retrieve_metadata(self, artifact_path: str) -> ArtifactMetadata:
        record = self._read_file_blob_metadata(artifact_path)
        blob_path = self._get_blob_data_path(artifact_path, record.blob_id)
        if not blob_path.is_file():
            raise ArtifactNotFoundError(f"Artifact content not found: {artifact_path}")
        return record.to_artifact_metadata()

    def retrieve_artifact(self, artifact_path: str) -> StoredArtifact:
        record = self._read_file_blob_metadata(artifact_path)
        content = self._read_content(artifact_path, record)
        return StoredArtifact(content=content, metadata=record.to_artifact_metadata())

    def exists(self, artifact_path: str) -> bool:
        self._validate_artifact_path(artifact_path)
        metadata_path = self._get_metadata_path(artifact_path)
        return metadata_path.is_file()
