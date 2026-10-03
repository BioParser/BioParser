from __future__ import annotations


class StorageError(Exception):
    """Base exception for all storage-related errors."""


class ArtifactNotFoundError(StorageError, KeyError):
    """Raised when an artifact cannot be found in storage."""


class ArtifactAlreadyExistsError(StorageError, FileExistsError):
    """Raised when attempting to store an artifact that already exists."""


class StoragePathTraversalError(StorageError, ValueError):
    """Raised when an Artifact path attempts to escape the configured storage root."""


class InvalidArtifactPathError(StorageError, ValueError):
    """Raised when an Artifact path is malformed or invalid."""


class StorageConfigurationError(StorageError, ValueError):
    """Raised when the storage backend configuration is invalid."""


class StoragePayloadError(StorageError, ValueError):
    """Raised when the artifact payload is invalid or does not match metadata."""
