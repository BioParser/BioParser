from __future__ import annotations

from typing import Protocol, runtime_checkable

from bioparser.storage.models import ArtifactMetadata, ArtifactRef, StoredArtifact


@runtime_checkable
class ArtifactStorage(Protocol):
    """Replaceable storage interface for binary payloads and artifact metadata.

    The interface is completely independent of transport-specific and filesystem paths.
    Artifacts are stored, retrieved, and checked strictly by artifact ID or reference.
    """

    def store(
        self,
        content: bytes,
        metadata: ArtifactMetadata,
        *,
        overwrite: bool = False,
    ) -> ArtifactRef:
        """Store an artifact's content payload and metadata.

        Args:
            content: The binary payload of the artifact.
            metadata: Metadata describing the artifact, including artifact_id and document_id.
            overwrite: If False, raises ArtifactAlreadyExistsError if an artifact with
                this ID already exists. If True, overwrites existing artifact.

        Returns:
            An ArtifactRef pointing to the stored artifact.
        """
        ...

    def retrieve(self, artifact_id: str | ArtifactRef) -> bytes:
        """Retrieve the binary payload of an artifact.

        Args:
            artifact_id: The string identifier or ArtifactRef.

        Returns:
            The raw bytes of the stored artifact.

        Raises:
            ArtifactNotFoundError: If no artifact exists with the given ID.
            StoragePathTraversalError: If the ID attempts path traversal.
        """
        ...

    def retrieve_metadata(self, artifact_id: str | ArtifactRef) -> ArtifactMetadata:
        """Retrieve the metadata of an artifact.

        Args:
            artifact_id: The string identifier or ArtifactRef.

        Returns:
            ArtifactMetadata describing the artifact.

        Raises:
            ArtifactNotFoundError: If no artifact exists with the given ID.
            StoragePathTraversalError: If the ID attempts path traversal.
        """
        ...

    def retrieve_artifact(self, artifact_id: str | ArtifactRef) -> StoredArtifact:
        """Retrieve both content and metadata for an artifact in a single operation.

        Args:
            artifact_id: The string identifier or ArtifactRef.

        Returns:
            StoredArtifact bundle containing content and metadata.

        Raises:
            ArtifactNotFoundError: If no artifact exists with the given ID.
            StoragePathTraversalError: If the ID attempts path traversal.
        """
        ...

    def exists(self, artifact_id: str | ArtifactRef) -> bool:
        """Check whether an artifact exists in storage.

        Args:
            artifact_id: The string identifier or ArtifactRef.

        Returns:
            True if both content and metadata exist; False otherwise.

        Raises:
            StoragePathTraversalError: If the ID attempts path traversal.
        """
        ...
