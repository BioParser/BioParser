from typing import Literal

from pydantic import BaseModel, ConfigDict

JobStatus = Literal["queued", "running", "succeeded", "failed"]

#: Schema version for the JobState contract. Bump when the shape of JobState
#: changes in a way that is not backward compatible, so a stored record from
#: an older version is recognized as such rather than silently misread.

JOB_STATE_SCHEMA_VERSION: Literal[1] = 1


class JobState(BaseModel):
    """Typed, versioned state for a single parse/extract job."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = JOB_STATE_SCHEMA_VERSION
    status: JobStatus
