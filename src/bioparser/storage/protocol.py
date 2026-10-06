from __future__ import annotations

from typing import Protocol, runtime_checkable

from bioparser.storage.models import ArtifactMetadata, StoredArtifact


@runtime_checkable
class ArtifactStorage(Protocol):
    """Replaceable storage interface for binary payloads and artifact metadata.

    The interface is completely independent of transport-specific and filesystem paths.
    Artifacts are stored, retrieved, and checked strictly by Artifact path string.
    """

    def store(
        self,
        content: bytes,
        metadata: ArtifactMetadata,
    ) -> str:
        """Store an artifact's content payload and metadata.

        Args:
            content: The binary payload of the artifact.
            metadata: Metadata describing the artifact.

        Returns:
            The Artifact path string.
        """
        ...

    def retrieve(self, artifact_path: str) -> bytes:
        """Retrieve the binary payload of an artifact.

        Args:
            artifact_path: The string identifier of the artifact.

        Returns:
            The raw bytes of the stored artifact.

        Raises:
            ArtifactNotFoundError: If no artifact exists with the given ID.
            StoragePathTraversalError: If the ID attempts path traversal.
        """
        ...

    def retrieve_metadata(self, artifact_path: str) -> ArtifactMetadata:
        """Retrieve the metadata of an artifact.

        Args:
            artifact_path: The string identifier of the artifact.

        Returns:
            ArtifactMetadata describing the artifact.

        Raises:
            ArtifactNotFoundError: If no artifact exists with the given ID.
            StoragePathTraversalError: If the ID attempts path traversal.
        """
        ...

    def retrieve_artifact(self, artifact_path: str) -> StoredArtifact:
        """Retrieve both content and metadata for an artifact in a single operation.

        Args:
            artifact_path: The string identifier of the artifact.

        Returns:
            StoredArtifact bundle containing content and metadata.

        Raises:
            ArtifactNotFoundError: If no artifact exists with the given ID.
            StoragePathTraversalError: If the ID attempts path traversal.
        """
        ...

    def exists(self, artifact_path: str) -> bool:
        """Check whether an artifact exists in storage.

        Args:
            artifact_path: The string identifier of the artifact.

        Returns:
            True if the artifact exists; False otherwise.

        Raises:
            StoragePathTraversalError: If the ID attempts path traversal.
        """
        ...
