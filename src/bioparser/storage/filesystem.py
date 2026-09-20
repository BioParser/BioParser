from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from bioparser.storage.errors import (
    ArtifactAlreadyExistsError,
    ArtifactNotFoundError,
    InvalidArtifactIdError,
    StorageConfigurationError,
    StoragePathTraversalError,
)
from bioparser.storage.models import (
    ArtifactMetadata,
    ArtifactRef,
    StoredArtifact,
)


class FileSystemArtifactStorage:
    """Local filesystem implementation of the ArtifactStorage interface.

    Stores artifacts within a configurable root directory. Artifacts are isolated
    by their artifact_id into subdirectories containing the binary content and JSON
    metadata. All operations validate that generated paths remain strictly within
    the storage root.
    """

    def __init__(self, root_path: Path | str, *, create_root: bool = True) -> None:
        self.root_path = Path(root_path).resolve()
        if create_root:
            self.root_path.mkdir(parents=True, exist_ok=True)
        elif not self.root_path.is_dir():
            raise StorageConfigurationError(
                f"Configured storage root directory does not exist: {self.root_path}"
            )

    def _resolve_id(self, artifact_id: str | ArtifactRef) -> str:
        if isinstance(artifact_id, ArtifactRef):
            return artifact_id.artifact_id
        return str(artifact_id)

    def _validate_artifact_id(self, artifact_id: str) -> None:
        if not artifact_id or not artifact_id.strip():
            raise InvalidArtifactIdError("Artifact ID cannot be empty or whitespace")
        if "\0" in artifact_id:
            raise InvalidArtifactIdError("Artifact ID cannot contain null bytes")
        if ":" in artifact_id:
            raise StoragePathTraversalError("Artifact ID cannot contain drive or scheme separators")

        parts = Path(artifact_id).parts
        if any(part in ("..", ".", "~") for part in parts):
            raise StoragePathTraversalError("Artifact ID cannot contain path navigation tokens")
        if Path(artifact_id).is_absolute() or artifact_id.startswith(("/", "\\")):
            raise StoragePathTraversalError("Artifact ID cannot be an absolute path")

    def _get_artifact_dir(self, artifact_id: str) -> Path:
        self._validate_artifact_id(artifact_id)
        target_dir = (self.root_path / artifact_id).resolve()
        try:
            is_relative = target_dir.is_relative_to(self.root_path)
        except AttributeError:
            is_relative = str(target_dir).startswith(str(self.root_path))

        if not is_relative or target_dir == self.root_path:
            raise StoragePathTraversalError(
                f"Generated artifact path escapes storage root: {artifact_id}"
            )
        return target_dir

    def store(
        self,
        content: bytes,
        metadata: ArtifactMetadata,
        *,
        overwrite: bool = False,
    ) -> ArtifactRef:
        aid = metadata.artifact_id
        artifact_dir = self._get_artifact_dir(aid)

        if not overwrite and self.exists(aid):
            raise ArtifactAlreadyExistsError(f"Artifact already exists: {aid}")

        if metadata.size_bytes is None:
            metadata = metadata.model_copy(update={"size_bytes": len(content)})
        elif metadata.size_bytes != len(content):
            raise ValueError(
                f"Metadata size_bytes ({metadata.size_bytes}) does not match "
                f"content length ({len(content)})"
            )

        parent_dir = artifact_dir.parent
        parent_dir.mkdir(parents=True, exist_ok=True)

        staging_dir = parent_dir / f".tmp_{artifact_dir.name}_{uuid.uuid4().hex}"
        staging_dir.mkdir(parents=True, exist_ok=False)

        try:
            (staging_dir / "content").write_bytes(content)
            (staging_dir / "metadata.json").write_text(
                metadata.model_dump_json(indent=2), encoding="utf-8"
            )

            if not overwrite:
                try:
                    os.rename(staging_dir, artifact_dir)
                except (FileExistsError, OSError) as exc:
                    raise ArtifactAlreadyExistsError(f"Artifact already exists: {aid}") from exc
            else:
                try:
                    os.replace(staging_dir, artifact_dir)
                except (PermissionError, OSError):
                    if artifact_dir.exists():
                        backup_dir = parent_dir / f".bak_{artifact_dir.name}_{uuid.uuid4().hex}"
                        os.rename(artifact_dir, backup_dir)
                        try:
                            os.rename(staging_dir, artifact_dir)
                            shutil.rmtree(backup_dir, ignore_errors=True)
                        except Exception:
                            if backup_dir.exists() and not artifact_dir.exists():
                                os.rename(backup_dir, artifact_dir)
                            raise
                    else:
                        os.rename(staging_dir, artifact_dir)
        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)

        return ArtifactRef(artifact_id=aid)

    def retrieve(self, artifact_id: str | ArtifactRef) -> bytes:
        aid = self._resolve_id(artifact_id)
        artifact_dir = self._get_artifact_dir(aid)
        content_path = artifact_dir / "content"

        if not content_path.is_file():
            raise ArtifactNotFoundError(f"Artifact content not found: {aid}")

        return content_path.read_bytes()

    def retrieve_metadata(self, artifact_id: str | ArtifactRef) -> ArtifactMetadata:
        aid = self._resolve_id(artifact_id)
        artifact_dir = self._get_artifact_dir(aid)
        metadata_path = artifact_dir / "metadata.json"

        if not metadata_path.is_file():
            raise ArtifactNotFoundError(f"Artifact metadata not found: {aid}")

        raw_json = metadata_path.read_text(encoding="utf-8")
        return ArtifactMetadata.model_validate_json(raw_json)

    def retrieve_artifact(self, artifact_id: str | ArtifactRef) -> StoredArtifact:
        aid = self._resolve_id(artifact_id)
        content = self.retrieve(aid)
        metadata = self.retrieve_metadata(aid)
        return StoredArtifact(content=content, metadata=metadata)

    def exists(self, artifact_id: str | ArtifactRef) -> bool:
        aid = self._resolve_id(artifact_id)
        artifact_dir = self._get_artifact_dir(aid)
        return (artifact_dir / "content").is_file() and (artifact_dir / "metadata.json").is_file()
