from typing import Literal, Self

from pydantic import UUID4, BaseModel, ConfigDict, Field, model_validator

JobStatus = Literal["queued", "running", "succeeded", "failed"]
SafeErrorCode = Literal[
    "invalid_pdf", "parse_failed", "extraction_failed", "timeout", "internal_error"
]

#: Schema version for the JobState contract. Bump the version up when the shape of JobState
#: changes in a way that is not backward compatible, so a stored record from
#: an older version is recognized as such rather than silently misread.
JOB_STATE_SCHEMA_VERSION: Literal[1] = 1


class SafeError(BaseModel):
    """A short, non-sensitive description of why a job failed."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    code: SafeErrorCode


class JobState(BaseModel):
    """Typed, versioned state for a single parse/extract job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = JOB_STATE_SCHEMA_VERSION
    job_id: UUID4
    document_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: JobStatus
    input_artifact_ref: str = Field(min_length=1)
    output_artifact_ref: str | None = None
    error: SafeError | None = None

    def with_changes(self, **changes: object) -> Self:
        """Return a new, validated JobState with the given fields changed.
        Use instead of model_copy(update=...), which skips validation
        entirely and can silently produce a JobState that violates
        _terminal_fields_match_status.
        """
        return self.model_validate(self.model_dump() | changes)

    @model_validator(mode="after")
    def _terminal_fields_match_status(self) -> Self:
        if self.status in ("queued", "running"):
            if self.output_artifact_ref is not None or self.error is not None:
                raise ValueError(
                    f"status={self.status!r} must not carry an output artifact or error"
                )
        elif self.status == "succeeded":
            if self.output_artifact_ref is None:
                raise ValueError("status='succeeded' requires an output_artifact_ref")
            if self.error is not None:
                raise ValueError("status='succeeded' must not carry an error")
        elif self.status == "failed":
            if self.error is None:
                raise ValueError("status='failed' requires an error")
            if self.output_artifact_ref is not None:
                raise ValueError("status='failed' must not carry an output artifact")
        return self
