"""Shared shape rules and the naming convention for artifact paths.

An artifact path is the key under which an artifact is stored. Storage validates against these
rules. The job state uses the same rules, and the job queue is meant to (#127), so a malformed
path is rejected at whichever boundary it enters.

Convention (build paths with the functions below, never by string formatting):

- ``pdf/<pdf-checksum>``: an uploaded PDF.
- ``pdf/<pdf-checksum>/parsed/<backend>/<version>``: the parser artifact made from that PDF
  by parser backend ``<backend>`` at backend version ``<version>``.

This module only defines what a well-formed path looks like and how to build one. It has no
dependency on storage, so the job modules can use it without importing the storage package.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator

ARTIFACT_PATH_MAX_LENGTH = 512

# 255-byte filename limit minus the longest suffix storage appends (".<32 hex>.data" = 38),
# rounded down for margin.
ARTIFACT_PATH_MAX_SEGMENT_LENGTH = 200

_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")


class ArtifactPathError(ValueError):
    """Raised when an artifact path is malformed."""


class ArtifactPathTraversalError(ArtifactPathError):
    """Raised when an artifact path contains a '.' or '..' segment."""


def validate_artifact_path(value: str) -> str:
    """Return ``value`` unchanged if it is a well-formed artifact path.

    A path is one or more segments separated by single ``/`` characters. Each segment is
    non-empty, at most ``ARTIFACT_PATH_MAX_SEGMENT_LENGTH`` characters and made only of ASCII
    letters, digits, ``_``, ``.`` and ``-``. A segment may not be ``.`` or ``..``. The whole path
    is at most ``ARTIFACT_PATH_MAX_LENGTH`` characters and has no leading or trailing ``/``.

    Raises:
        ArtifactPathTraversalError: A segment is ``.`` or ``..``.
        ArtifactPathError: The path is not a string or violates any other rule.
    """
    if not isinstance(value, str):
        raise ArtifactPathError("Artifact path must be a string")
    if not value:
        raise ArtifactPathError("Artifact path cannot be empty")
    if len(value) > ARTIFACT_PATH_MAX_LENGTH:
        raise ArtifactPathError(
            f"Artifact path exceeds maximum length of {ARTIFACT_PATH_MAX_LENGTH} characters"
        )
    segments = value.split("/")
    if "" in segments:
        raise ArtifactPathError(
            "Artifact path must not have empty segments (leading, trailing or doubled '/')"
        )
    if any(segment in (".", "..") for segment in segments):
        raise ArtifactPathTraversalError("Artifact path cannot contain '.' or '..' path segments")
    if any(len(segment) > ARTIFACT_PATH_MAX_SEGMENT_LENGTH for segment in segments):
        raise ArtifactPathError(
            f"Artifact path contains a segment exceeding {ARTIFACT_PATH_MAX_SEGMENT_LENGTH} "
            "characters"
        )
    if not all(_SEGMENT_PATTERN.fullmatch(segment) for segment in segments):
        raise ArtifactPathError(
            f"Artifact path '{value}' contains invalid characters. "
            "Only alphanumeric characters, '/', '.', '_' and '-' are allowed."
        )
    return value


#: Field type for an artifact path: a ``str`` validated by ``validate_artifact_path``.
ArtifactPath = Annotated[str, AfterValidator(validate_artifact_path)]


def _path_segment(name: str, value: str) -> str:
    """Return ``value`` if it is one valid path segment, so a builder argument cannot add
    or remove segments (``"a/b"``, ``".."``, ``""``)."""
    if not isinstance(value, str) or "/" in value:
        raise ArtifactPathError(f"{name} must be a single path segment")
    validate_artifact_path(value)
    return value


def pdf_path(checksum: str) -> str:
    """Return the artifact path of an uploaded PDF: ``pdf/<checksum>``."""
    return validate_artifact_path(f"pdf/{_path_segment('checksum', checksum)}")


def parse_result_path(checksum: str, backend: str, version: str) -> str:
    """Return the path of the parser artifact for a PDF:
    ``pdf/<checksum>/parsed/<backend>/<version>``."""
    return validate_artifact_path(
        f"{pdf_path(checksum)}/parsed/"
        f"{_path_segment('backend', backend)}/{_path_segment('version', version)}"
    )
