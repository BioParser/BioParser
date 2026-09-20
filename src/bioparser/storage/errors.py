from __future__ import annotations


class StorageError(Exception):
    """Base exception for all storage-related errors."""


class ArtifactNotFoundError(StorageError, KeyError):
    """Raised when an artifact cannot be found in storage."""


class ArtifactAlreadyExistsError(StorageError, FileExistsError):
    """Raised when attempting to overwrite an existing artifact without permission."""


class StoragePathTraversalError(StorageError, ValueError):
    """Raised when an artifact ID attempts to escape the configured storage root."""


class InvalidArtifactIdError(StorageError, ValueError):
    """Raised when an artifact ID is malformed or invalid."""


class StorageConfigurationError(StorageError, ValueError):
    """Raised when the storage backend configuration is invalid."""
