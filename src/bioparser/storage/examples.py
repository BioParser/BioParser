from __future__ import annotations

from datetime import UTC, datetime

from bioparser.storage.models import ArtifactMetadata, ArtifactRef, CreationInfo

SAMPLE_PDF_METADATA_JSON = """{
  "schema_version": "1",
  "artifact_id": "art-pdf-1002000",
  "document_id": "doc-plos-1002000",
  "media_type": "application/pdf",
  "checksum": "3fa85f6457174562b3fc2c963f66afa6e3b0c44298fc1c149afbf4c8996fb924",
  "creation_info": {
    "created_at": "2026-09-17T12:00:00Z",
    "created_by": "api-upload"
  },
  "content_schema_version": null,
  "size_bytes": 1048576
}"""

SAMPLE_PARSER_METADATA_JSON = """{
  "schema_version": "1",
  "artifact_id": "art-parsed-1002000",
  "document_id": "doc-plos-1002000",
  "media_type": "application/json",
  "checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "creation_info": {
    "created_at": "2026-09-17T12:05:00Z",
    "created_by": "parser-worker"
  },
  "content_schema_version": "1",
  "size_bytes": 24576
}"""

SAMPLE_EXTRACTOR_METADATA_JSON = """{
  "schema_version": "1",
  "artifact_id": "art-extracted-1002000",
  "document_id": "doc-plos-1002000",
  "media_type": "application/json",
  "checksum": "a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e",
  "creation_info": {
    "created_at": "2026-09-17T12:10:00Z",
    "created_by": "extractor-worker"
  },
  "content_schema_version": "1",
  "size_bytes": 4096
}"""


def sample_pdf_metadata() -> ArtifactMetadata:
    """Example metadata for an uploaded raw PDF artifact."""
    return ArtifactMetadata(
        schema_version="1",
        artifact_id="art-pdf-1002000",
        document_id="doc-plos-1002000",
        media_type="application/pdf",
        checksum="3fa85f6457174562b3fc2c963f66afa6e3b0c44298fc1c149afbf4c8996fb924",
        creation_info=CreationInfo(
            created_at=datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC),
            created_by="api-upload",
        ),
        content_schema_version=None,
        size_bytes=1048576,
    )


def sample_parser_metadata() -> ArtifactMetadata:
    """Example metadata for a structured parser artifact output."""
    return ArtifactMetadata(
        schema_version="1",
        artifact_id="art-parsed-1002000",
        document_id="doc-plos-1002000",
        media_type="application/json",
        checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        creation_info=CreationInfo(
            created_at=datetime(2026, 9, 17, 12, 5, 0, tzinfo=UTC),
            created_by="parser-worker",
        ),
        content_schema_version="1",
        size_bytes=24576,
    )


def sample_extractor_metadata() -> ArtifactMetadata:
    """Example metadata for an extractor candidate observations artifact."""
    return ArtifactMetadata(
        schema_version="1",
        artifact_id="art-extracted-1002000",
        document_id="doc-plos-1002000",
        media_type="application/json",
        checksum="a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e",
        creation_info=CreationInfo(
            created_at=datetime(2026, 9, 17, 12, 10, 0, tzinfo=UTC),
            created_by="extractor-worker",
        ),
        content_schema_version="1",
        size_bytes=4096,
    )


def sample_pdf_ref() -> ArtifactRef:
    """Example reference to a PDF artifact."""
    return ArtifactRef(artifact_id="art-pdf-1002000")


def sample_parser_ref() -> ArtifactRef:
    """Example reference to a parser output artifact."""
    return ArtifactRef(artifact_id="art-parsed-1002000")


def sample_extractor_ref() -> ArtifactRef:
    """Example reference to an extractor output artifact."""
    return ArtifactRef(artifact_id="art-extracted-1002000")
