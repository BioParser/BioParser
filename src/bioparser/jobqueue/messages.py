from typing import Literal

from pydantic import UUID4, BaseModel, ConfigDict

_PARSE_JOB_SCHEMA_VERSION: Literal[1] = 1


class ParseJobMessage(BaseModel):
    """Wake-up for one stored job. The job record holds the PDF id and parser."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = _PARSE_JOB_SCHEMA_VERSION
    job_id: UUID4
