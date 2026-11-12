from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from bioparser.ets.adapter import EtsSourceRow
from bioparser.ets.models import EtsRow
from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME, EXTRACTORS
from bioparser.hashing import Sha256Hex
from bioparser.jobs import SafeError
from bioparser.parser_names import DEFAULT_PARSER_NAME, ParserName
from bioparser.traits.table import Domain, Trait
from bioparser.traits.traitlist import TraitlistRow


class JobRecord(BaseModel):
    """Fields a `detail=full` body adds. The status already says which artifacts exist."""

    created_at: AwareDatetime
    display_name: str | None
    parser: str
    extractor: str
    document_checksum: str


class HealthResponse(BaseModel):
    status: Literal["ok"]

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok"}]})


class UploadLimitsResponse(BaseModel):
    max_upload_bytes: int = Field(description="Largest PDF the API accepts, in bytes")

    model_config = ConfigDict(json_schema_extra={"examples": [{"max_upload_bytes": 20971520}]})


class ReadinessResponse(BaseModel):
    status: Literal["ready"]

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ready"}]})


class ReadinessUnavailableDetail(BaseModel):
    status: Literal["not ready"]
    unavailable: list[str]


class ReadinessUnavailableResponse(BaseModel):
    detail: ReadinessUnavailableDetail

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "detail": {
                        "status": "not ready",
                        "unavailable": ["redis", "postgres"],
                    }
                }
            ]
        }
    )


class TraitListEts(BaseModel):
    """The trait list as ETS Traitlist rows, plus the list version."""

    model_config = ConfigDict(extra="forbid")

    version: str
    traits: tuple[TraitlistRow, ...]


class TraitListFull(BaseModel):
    """Full entries for one query.

    This is not a closed list. A deprecated trait's replacement may sit outside
    the selection, so it is not required to be one of these traits.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = Field(
        pattern=r"^\d{4}\.\d{2}\.\d+$",
        description=(
            "YYYY.MM.REVISION. The year and month the release was made, then a revision "
            "that starts at 0 and counts further releases in that month."
        ),
    )
    id_prefixes: dict[str, str | None]
    domains: tuple[Domain, ...]
    traits: tuple[Trait, ...]


class AcceptedJobResponse(BaseModel):
    """The 202 body from queueing a job. A later read carries the stored record."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    status: Literal["queued"]


class OpenJobResponse(BaseModel):
    """A job that is not done and has not failed. It has no observations."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    status: Literal["queued", "parsing", "parsed", "extracting"]


class OpenJobFullResponse(OpenJobResponse, JobRecord):
    """An open job with the stored record beside the status."""


class ListedJobResponse(BaseModel):
    """One job in a list. A done job does not include its ETS rows."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    status: Literal["queued", "parsing", "parsed", "extracting", "done"]


class ListedJobFullResponse(ListedJobResponse, JobRecord):
    """One listed job with the stored record. Still no ETS rows."""


class _DoneJob(BaseModel):
    """Fields shared by the two finished-job bodies. The row type differs."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    status: Literal["done"]
    # The mammal trait list that filled traitName and traitID on these rows.
    trait_list_version: str


class DoneJobResponse(_DoneJob):
    """A finished job: the ETS rows built from its extraction artifact."""

    rows: list[EtsRow]


class DoneJobFullResponse(_DoneJob, JobRecord):
    """A finished job with the record and the observation index."""

    rows: list[EtsSourceRow]


class FailedJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    status: Literal["failed"]
    error: SafeError


class FailedJobFullResponse(FailedJobResponse, JobRecord):
    """A failed job with the stored record beside the error."""


OpenJob = OpenJobResponse | OpenJobFullResponse
DoneJob = DoneJobResponse | DoneJobFullResponse
FailedJob = FailedJobResponse | FailedJobFullResponse
ListedJob = ListedJobResponse | ListedJobFullResponse

JobStatusResponse = OpenJob | DoneJob | FailedJob
JobListResponse = ListedJob | FailedJob


class DocumentMetadataResponse(BaseModel):
    checksum: Sha256Hex
    display_name: str | None = None
    upload_time: datetime
    size_bytes: int

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                    "display_name": "sample.pdf",
                    "upload_time": "2026-10-26T12:00:00Z",
                    "size_bytes": 1048576,
                }
            ]
        }
    )


class CreateJobRequest(BaseModel):
    # A Literal, so the OpenAPI schema lists the parser names and the frontend's
    # generated types do too.
    parser: ParserName = Field(default=DEFAULT_PARSER_NAME, description="Parser backend name")
    extractor: str = Field(default=DEFAULT_EXTRACTOR_NAME, description="Extractor backend name")

    @field_validator("extractor")
    @classmethod
    def validate_extractor(cls, value: str) -> str:
        if value not in EXTRACTORS:
            raise ValueError(f"must be one of: {', '.join(EXTRACTORS)}")
        return value

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "parser": "default",
                    "extractor": "default",
                }
            ]
        },
    )
