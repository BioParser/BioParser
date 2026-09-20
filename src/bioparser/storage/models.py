from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ARTIFACT_REF_SCHEMA_VERSION: Literal["1"] = "1"
ARTIFACT_METADATA_SCHEMA_VERSION: Literal["1"] = "1"


class CreationInfo(BaseModel):
    """Information about when and by what component an artifact was created."""

    model_config = ConfigDict(extra="forbid")

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str | None = None

    @field_validator("created_at", mode="after")
    @classmethod
    def _ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("created_at must be timezone-aware (UTC)")
        return v.astimezone(UTC)


class ArtifactRef(BaseModel):
    """Reference to an artifact, used to address artifacts across jobs and workers."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    schema_version: Literal["1"] = ARTIFACT_REF_SCHEMA_VERSION


class ArtifactMetadata(BaseModel):
    """Metadata describing an artifact's identity, provenance, format, and checksum."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = ARTIFACT_METADATA_SCHEMA_VERSION
    artifact_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    checksum: str | None = None
    creation_info: CreationInfo = Field(default_factory=CreationInfo)
    content_schema_version: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _coerce_creation_fields(cls, data: Any) -> Any:
        if (
            isinstance(data, dict)
            and "creation_info" not in data
            and ("created_at" in data or "created_by" in data)
        ):
            data = data.copy()
            created_at = data.pop("created_at", None)
            created_by = data.pop("created_by", None)
            creation_dict: dict[str, Any] = {}
            if created_at is not None:
                creation_dict["created_at"] = created_at
            if created_by is not None:
                creation_dict["created_by"] = created_by
            data["creation_info"] = creation_dict
        return data

    @property
    def created_at(self) -> datetime:
        """Timestamp when the artifact was created."""
        return self.creation_info.created_at

    @property
    def created_by(self) -> str | None:
        """Identifier of the service or component that produced this artifact."""
        return self.creation_info.created_by


class StoredArtifact(BaseModel):
    """Artifact bundle containing both its content payload and metadata."""

    model_config = ConfigDict(extra="forbid")

    content: bytes
    metadata: ArtifactMetadata

    @property
    def artifact_id(self) -> str:
        return self.metadata.artifact_id

    @property
    def ref(self) -> ArtifactRef:
        return ArtifactRef(artifact_id=self.metadata.artifact_id)
