"""Shared shape rules and the naming convention for artifact paths.

An artifact path is the key under which an artifact is stored. See "Artifact paths" in docs/architecture.md.

Convention (build paths with the functions below, never by string formatting):

- ``pdf/<pdf-checksum>``: an uploaded PDF.
- ``pdf/<pdf-checksum>/parsed/<backend-segments>``: the parser artifact made from that PDF.
  The segments are backend-defined (typically backend name, version, and any configuration
  or job-scoped segments). When any segment exceeds ``ARTIFACT_PATH_MAX_SEGMENT_LENGTH`` or
  the full path would exceed ``ARTIFACT_PATH_MAX_LENGTH``, the segments are replaced by a
  single sha256 hex digest segment.
- ``<parse-result-path>/extracted/<extractor-segments>``: the extraction artifact made from
  that parser artifact. It keeps every segment of the parse path, followed by the
  extractor-defined segments (the ``default`` extractor includes ``<job-id>`` so each job
  stores its own extraction result; segments are hashed into one sha256 hex digest if too
  long).

This module only defines what a well-formed path looks like and how to build one. It has no
dependency on storage, so the job modules can use it without importing the storage package.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Annotated

from pydantic import AfterValidator

from bioparser.hashing import sha256_bytes

ARTIFACT_PATH_MAX_LENGTH = 512

# 255-byte filename limit minus the longest suffix storage appends (".<32 hex>.data" = 38),
# rounded down for margin.
ARTIFACT_PATH_MAX_SEGMENT_LENGTH = 200

_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")


class ArtifactPathError(ValueError):
    """Raised when an artifact path is malformed."""


class ArtifactPathTraversalError(ArtifactPathError):
    """Raised when an artifact path contains a '.' or '..' segment."""


def validate_path_segment(value: str) -> str:
    """Return ``value`` unchanged if it is one well-formed artifact path segment.

    A segment is non-empty, at most ``ARTIFACT_PATH_MAX_SEGMENT_LENGTH`` characters and made
    only of ASCII letters, digits, ``_``, ``.`` and ``-``. It may not be ``.`` or ``..``. It
    cannot contain ``/``, so a builder argument cannot add or remove segments.

    Raises:
        ArtifactPathTraversalError: The segment is ``.`` or ``..``.
        ArtifactPathError: The segment is not a string or violates any other rule.
    """
    if not isinstance(value, str):
        raise ArtifactPathError("Artifact path segment must be a string")
    if not value:
        raise ArtifactPathError("Artifact path segment cannot be empty")
    if value in (".", ".."):
        raise ArtifactPathTraversalError("Artifact path cannot contain '.' or '..' path segments")
    if len(value) > ARTIFACT_PATH_MAX_SEGMENT_LENGTH:
        raise ArtifactPathError(
            f"Artifact path contains a segment exceeding {ARTIFACT_PATH_MAX_SEGMENT_LENGTH} "
            "characters"
        )
    if not _SEGMENT_PATTERN.fullmatch(value):
        raise ArtifactPathError(
            "Artifact path segment contains invalid characters. "
            "Only alphanumeric characters, '.', '_' and '-' are allowed."
        )
    return value


def validate_artifact_path(value: str) -> str:
    """Return ``value`` unchanged if it is a well-formed artifact path.

    A path is one or more segments separated by single ``/`` characters, each valid under
    ``validate_path_segment``. The whole path is at most ``ARTIFACT_PATH_MAX_LENGTH`` characters
    and has no leading or trailing ``/``.

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
    for segment in segments:
        validate_path_segment(segment)
    return value


#: Field type for an artifact path: a ``str`` validated by ``validate_artifact_path``.
ArtifactPath = Annotated[str, AfterValidator(validate_artifact_path)]


def pdf_path(checksum: str) -> str:
    """Return the artifact path of an uploaded PDF: ``pdf/<checksum>``."""
    return validate_artifact_path(f"pdf/{validate_path_segment(checksum)}")


def _hash_segments(segments: Sequence[str]) -> str:
    encoded = json.dumps(list(segments), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256_bytes(encoded)


def _append_segments(prefix: str, segments: Sequence[str]) -> str:
    if isinstance(segments, str | bytes) or not isinstance(segments, Sequence):
        raise ArtifactPathError("Artifact path segments must be a sequence of strings")
    segment_list = list(segments)
    if not segment_list:
        raise ArtifactPathError("Artifact path requires at least one backend segment")
    for segment in segment_list:
        if not isinstance(segment, str):
            raise ArtifactPathError("Artifact path segment must be a string")
        if not segment:
            raise ArtifactPathError("Artifact path segment cannot be empty")
        if segment in (".", ".."):
            raise ArtifactPathTraversalError(
                "Artifact path cannot contain '.' or '..' path segments"
            )

    candidate = f"{prefix}/{'/'.join(segment_list)}"
    too_long_segment = any(
        len(segment) > ARTIFACT_PATH_MAX_SEGMENT_LENGTH for segment in segment_list
    )
    if too_long_segment or len(candidate) > ARTIFACT_PATH_MAX_LENGTH:
        return validate_artifact_path(f"{prefix}/{_hash_segments(segment_list)}")

    for segment in segment_list:
        validate_path_segment(segment)
    return validate_artifact_path(candidate)


def parse_result_path(checksum: str, segments: Sequence[str]) -> str:
    """Return the path of the parser artifact for a PDF:
    ``pdf/<checksum>/parsed/<segments...>``.

    ``segments`` is defined by the parser backend (typically name, version, and any
    configuration or job-scoped segments). When any segment exceeds
    ``ARTIFACT_PATH_MAX_SEGMENT_LENGTH`` or the combined path would exceed
    ``ARTIFACT_PATH_MAX_LENGTH``, the segments are stored as one sha256 hex digest segment.
    """
    return _append_segments(f"{pdf_path(checksum)}/parsed", segments)


def extract_result_path(parse_result_ref: str, segments: Sequence[str]) -> str:
    """Return the path of the extraction artifact for one parser artifact:
    ``<parse_result_ref>/extracted/<segments...>``.

    The parse path is kept whole, so every segment on it is part of the extraction path too.
    ``segments`` is defined by the extractor backend. When any segment exceeds
    ``ARTIFACT_PATH_MAX_SEGMENT_LENGTH`` or the combined path would exceed
    ``ARTIFACT_PATH_MAX_LENGTH``, the segments are stored as one sha256 hex digest segment.
    """
    parsed = validate_artifact_path(parse_result_ref)
    return _append_segments(f"{parsed}/extracted", segments)
