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

_ARTIFACT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAX_ARTIFACT_ID_LENGTH = 128


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
      - Payload content: `{artifact_id}.{blob_id}.data`
      - Metadata JSON:   `{artifact_id}.meta.json`

    Payloads are written as immutable blobs first, then the metadata JSON file is
    atomically linked (on create) or replaced (on overwrite) as the single commit point.
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

    def _validate_artifact_id(self, artifact_id: str) -> None:
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            raise InvalidArtifactIdError("Artifact ID cannot be empty or whitespace")
        if len(artifact_id) > _MAX_ARTIFACT_ID_LENGTH:
            raise InvalidArtifactIdError(
                f"Artifact ID exceeds maximum length of {_MAX_ARTIFACT_ID_LENGTH} characters: {artifact_id}"
            )
        if artifact_id in (".", ".."):
            raise StoragePathTraversalError("Artifact ID cannot be '.' or '..'")
        if not _ARTIFACT_ID_PATTERN.fullmatch(artifact_id):
            raise InvalidArtifactIdError(
                f"Artifact ID '{artifact_id}' contains invalid characters. "
                "Only alphanumeric characters, '.', '_', and '-' are allowed."
            )

    def _get_metadata_path(self, artifact_id: str) -> Path:
        self._validate_artifact_id(artifact_id)
        metadata_path = (self.root_path / f"{artifact_id}.meta.json").resolve()
        if not metadata_path.is_relative_to(self.root_path) or metadata_path == self.root_path:
            raise StoragePathTraversalError(
                f"Generated artifact path escapes storage root: {artifact_id}"
            )
        return metadata_path

    def _get_blob_data_path(self, artifact_id: str, blob_id: str) -> Path:
        self._validate_artifact_id(artifact_id)
        if not _ARTIFACT_ID_PATTERN.fullmatch(blob_id):
            raise StoragePathTraversalError(f"Invalid blob ID: {blob_id}")
        content_path = (self.root_path / f"{artifact_id}.{blob_id}.data").resolve()
        if not content_path.is_relative_to(self.root_path) or content_path == self.root_path:
            raise StoragePathTraversalError(
                f"Generated artifact path escapes storage root: {artifact_id}"
            )
        return content_path

    def _read_file_blob_metadata(self, artifact_id: str) -> FileBlobMetadata:
        metadata_path = self._get_metadata_path(artifact_id)
        if not metadata_path.is_file():
            raise ArtifactNotFoundError(f"Artifact metadata not found: {artifact_id}")

        try:
            raw_json = metadata_path.read_text(encoding="utf-8")
            return FileBlobMetadata.model_validate_json(raw_json)
        except (ValidationError, ValueError) as exc:
            raise StoragePayloadError(
                f"Corrupted metadata for artifact {artifact_id}: {exc}"
            ) from exc
        except OSError as exc:
            raise StorageError(f"Failed to read metadata for {artifact_id}: {exc}") from exc

    def _read_content(self, artifact_id: str, meta: FileBlobMetadata) -> bytes:
        data_path = self._get_blob_data_path(artifact_id, meta.blob_id)
        if not data_path.is_file():
            raise ArtifactNotFoundError(f"Artifact content not found: {artifact_id}")

        try:
            content = data_path.read_bytes()
        except OSError as exc:
            raise StorageError(f"Failed to read artifact {artifact_id}: {exc}") from exc

        if meta.size_bytes is not None and len(content) != meta.size_bytes:
            raise StoragePayloadError(
                f"Artifact {artifact_id} content size ({len(content)}) does not match "
                f"metadata size_bytes ({meta.size_bytes})"
            )

        return content

    def store(
        self,
        content: bytes,
        metadata: ArtifactMetadata,
        *,
        overwrite: bool = False,
    ) -> str:
        aid = metadata.artifact_id
        metadata_path = self._get_metadata_path(aid)

        if not overwrite and self.exists(aid):
            raise ArtifactAlreadyExistsError(f"Artifact already exists: {aid}")

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
        tmp_meta_path = self.root_path / f".tmp_{aid}_{blob_id}.meta.json"

        blob_written = False
        tmp_meta_written = False
        committed = False

        try:
            blob_data_path.write_bytes(content)
            blob_written = True

            tmp_meta_path.write_text(blob_meta.model_dump_json(indent=2), encoding="utf-8")
            tmp_meta_written = True

            if not overwrite:
                try:
                    os.link(tmp_meta_path, metadata_path)
                    committed = True
                except FileExistsError as exc:
                    raise ArtifactAlreadyExistsError(f"Artifact already exists: {aid}") from exc
            else:
                os.replace(tmp_meta_path, metadata_path)
                committed = True

        except (ArtifactAlreadyExistsError, StoragePayloadError):
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

    def retrieve(self, artifact_id: str) -> bytes:
        record = self._read_file_blob_metadata(artifact_id)
        return self._read_content(artifact_id, record)

    def retrieve_metadata(self, artifact_id: str) -> ArtifactMetadata:
        record = self._read_file_blob_metadata(artifact_id)
        blob_path = self._get_blob_data_path(artifact_id, record.blob_id)
        if not blob_path.is_file():
            raise ArtifactNotFoundError(f"Artifact content not found: {artifact_id}")
        return record.to_artifact_metadata()

    def retrieve_artifact(self, artifact_id: str) -> StoredArtifact:
        record = self._read_file_blob_metadata(artifact_id)
        content = self._read_content(artifact_id, record)
        return StoredArtifact(content=content, metadata=record.to_artifact_metadata())

    def exists(self, artifact_id: str) -> bool:
        self._validate_artifact_id(artifact_id)
        metadata_path = self._get_metadata_path(artifact_id)
        return metadata_path.is_file()
