from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

JobStatus = Literal["queued", "running", "succeeded", "failed"]

#: Schema version for the JobState contract. Bump the version up when the shape of JobState
#: changes in a way that is not backward compatible, so a stored record from
#: an older version is recognized as such rather than silently misread.
JOB_STATE_SCHEMA_VERSION: Literal[1] = 1

# MAX_SAFE_ERROR is for limiting the length of error messages stored in the job state. This is to avoid
# issues with storing very large error messages in the database, which can cause performance issues or
# even failures when trying to retrieve or process the job state. The limit is set to 500 characters,
# which is a reasonable size for an error message while still allowing
# for meaningful information to be conveyed.
_MAX_SAFE_ERROR_MESSAGE_LENGTH = 500


class SafeError(BaseModel):
    """A short, non-sensitive description of why a job failed."""

    model_config = ConfigDict(frozen=True)
    message: str = Field(min_length=1, max_length=_MAX_SAFE_ERROR_MESSAGE_LENGTH)


class JobState(BaseModel):
    """Typed, versioned state for a single parse/extract job."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = JOB_STATE_SCHEMA_VERSION
    job_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    status: JobStatus
    input_artifact_ref: str = Field(min_length=1)
    output_artifact_ref: str | None = None
    error: SafeError | None = None

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
