from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from bioparser.storage.models import (
    ARTIFACT_METADATA_SCHEMA_VERSION,
    ArtifactMetadata,
    CreationInfo,
    StoredArtifact,
)

from .contract_examples import (
    SAMPLE_EXTRACTOR_METADATA_JSON,
    SAMPLE_PARSER_METADATA_JSON,
    SAMPLE_PDF_METADATA_JSON,
    sample_extractor_metadata,
    sample_parser_metadata,
    sample_pdf_metadata,
)


class TestCreationInfo:
    def test_default_values(self) -> None:
        now_before = datetime.now(UTC)
        info = CreationInfo()
        now_after = datetime.now(UTC)

        assert info.created_by is None
        assert info.created_at.tzinfo is not None
        assert now_before <= info.created_at <= now_after

    def test_explicit_values(self) -> None:
        dt = datetime(2026, 9, 17, 10, 30, tzinfo=UTC)
        info = CreationInfo(created_at=dt, created_by="test-worker")
        assert info.created_at == dt
        assert info.created_by == "test-worker"

    def test_naive_datetime_rejected(self) -> None:
        naive_dt = datetime(2026, 9, 20, 10, 30)  # noqa: DTZ001
        with pytest.raises(ValidationError, match="timezone-aware"):
            CreationInfo(created_at=naive_dt)

    def test_non_utc_timezone_normalized_to_utc(self) -> None:
        offset_tz = timezone(timedelta(hours=2))
        offset_dt = datetime(2026, 9, 20, 12, 30, tzinfo=offset_tz)
        info = CreationInfo(created_at=offset_dt)
        assert info.created_at.tzinfo == UTC
        assert info.created_at.hour == 10
        assert info.created_at.minute == 30

    def test_forbid_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            CreationInfo.model_validate({"extra": 123})


class TestArtifactMetadata:
    def test_required_fields_and_defaults(self) -> None:
        meta = ArtifactMetadata(
            artifact_id="art-1",
            document_id="doc-1",
            media_type="application/pdf",
        )
        assert meta.schema_version == ARTIFACT_METADATA_SCHEMA_VERSION
        assert meta.schema_version == "1"
        assert meta.artifact_id == "art-1"
        assert meta.document_id == "doc-1"
        assert meta.media_type == "application/pdf"
        assert meta.checksum is None
        assert meta.content_schema_version is None
        assert meta.size_bytes is None
        assert isinstance(meta.creation_info, CreationInfo)
        assert meta.created_at == meta.creation_info.created_at
        assert meta.created_by is None

    def test_unsupported_metadata_schema_version_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactMetadata.model_validate(
                {
                    "schema_version": "2",
                    "artifact_id": "art-1",
                    "document_id": "doc-1",
                    "media_type": "application/pdf",
                }
            )

    def test_coercion_from_top_level_creation_fields(self) -> None:
        dt = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
        meta = ArtifactMetadata.model_validate(
            {
                "artifact_id": "art-2",
                "document_id": "doc-2",
                "media_type": "application/json",
                "created_at": dt,
                "created_by": "api-gateway",
            }
        )
        assert meta.creation_info.created_at == dt
        assert meta.creation_info.created_by == "api-gateway"
        assert meta.created_at == dt
        assert meta.created_by == "api-gateway"

    def test_coercion_does_not_mutate_caller_dict(self) -> None:
        dt = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
        orig = {
            "artifact_id": "art-2",
            "document_id": "doc-2",
            "media_type": "application/json",
            "created_at": dt,
            "created_by": "api-gateway",
        }
        ArtifactMetadata.model_validate(orig)
        assert "created_at" in orig
        assert "created_by" in orig
        assert orig["created_at"] == dt
        assert orig["created_by"] == "api-gateway"

    def test_checksum_stored_and_retrieved(self) -> None:
        sha = "3fa85f6457174562b3fc2c963f66afa6e3b0c44298fc1c149afbf4c8996fb924"
        meta = ArtifactMetadata(
            artifact_id="art-3",
            document_id="doc-3",
            media_type="application/pdf",
            checksum=sha,
        )
        assert meta.checksum == sha
        dumped = meta.model_dump_json()
        loaded = ArtifactMetadata.model_validate_json(dumped)
        assert loaded.checksum == sha

    def test_content_schema_version_for_structured_artifacts(self) -> None:
        meta = ArtifactMetadata(
            artifact_id="art-parsed",
            document_id="doc-1",
            media_type="application/json",
            content_schema_version="1",
        )
        assert meta.content_schema_version == "1"

    def test_negative_size_bytes_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactMetadata(
                artifact_id="art-1",
                document_id="doc-1",
                media_type="application/pdf",
                size_bytes=-1,
            )

    def test_missing_required_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactMetadata.model_validate({"document_id": "d1", "media_type": "application/pdf"})
        with pytest.raises(ValidationError):
            ArtifactMetadata.model_validate({"artifact_id": "a1", "media_type": "application/pdf"})
        with pytest.raises(ValidationError):
            ArtifactMetadata.model_validate({"artifact_id": "a1", "document_id": "d1"})

    def test_forbid_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactMetadata.model_validate(
                {
                    "artifact_id": "a1",
                    "document_id": "d1",
                    "media_type": "application/pdf",
                    "unknown_field": "value",
                }
            )

    def test_json_roundtrip(self) -> None:
        meta = ArtifactMetadata(
            artifact_id="art-rt",
            document_id="doc-rt",
            media_type="application/pdf",
            checksum="abc123sha",
            creation_info=CreationInfo(
                created_at=datetime(2026, 9, 17, 14, 0, tzinfo=UTC),
                created_by="worker-1",
            ),
            content_schema_version="1",
            size_bytes=4096,
        )
        dumped = meta.model_dump_json()
        loaded = ArtifactMetadata.model_validate_json(dumped)
        assert meta == loaded


class TestStoredArtifact:
    def test_properties(self) -> None:
        meta = ArtifactMetadata(
            artifact_id="art-stored",
            document_id="doc-stored",
            media_type="application/pdf",
            size_bytes=4,
        )
        artifact = StoredArtifact(content=b"test", metadata=meta)
        assert artifact.artifact_id == "art-stored"
        assert artifact.content == b"test"
        assert artifact.metadata == meta


class TestContractExamples:
    def test_sample_pdf_metadata_contract(self) -> None:
        meta = sample_pdf_metadata()
        assert meta.schema_version == "1"
        assert meta.media_type == "application/pdf"
        assert meta.content_schema_version is None
        assert meta.checksum is not None

        # Validate against example JSON
        from_json = ArtifactMetadata.model_validate_json(SAMPLE_PDF_METADATA_JSON)
        assert from_json.artifact_id == meta.artifact_id
        assert from_json.media_type == meta.media_type
        assert from_json.checksum == meta.checksum

    def test_sample_parser_metadata_contract(self) -> None:
        meta = sample_parser_metadata()
        assert meta.schema_version == "1"
        assert meta.media_type == "application/json"
        assert meta.content_schema_version == "1"

        from_json = ArtifactMetadata.model_validate_json(SAMPLE_PARSER_METADATA_JSON)
        assert from_json.content_schema_version == "1"
        assert from_json.artifact_id == meta.artifact_id

    def test_sample_extractor_metadata_contract(self) -> None:
        meta = sample_extractor_metadata()
        assert meta.schema_version == "1"
        assert meta.media_type == "application/json"
        assert meta.content_schema_version == "1"

        from_json = ArtifactMetadata.model_validate_json(SAMPLE_EXTRACTOR_METADATA_JSON)
        assert from_json.content_schema_version == "1"
        assert from_json.artifact_id == meta.artifact_id
