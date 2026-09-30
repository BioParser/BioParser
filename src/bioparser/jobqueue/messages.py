from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PARSE_JOB_SCHEMA_VERSION: Literal[1] = 1


class ParseJobMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = PARSE_JOB_SCHEMA_VERSION
    job_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    input_pdf_ref: str = Field(min_length=1)
