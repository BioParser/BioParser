from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal, Self

from pydantic import (
    UUID4,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from bioparser.artifact_paths import ArtifactPath
from bioparser.extractor_names import EXTRACTORS
from bioparser.hashing import Sha256Hex
from bioparser.parser_names import PARSER_BACKENDS


class JobStateError(RuntimeError):
    """A pipeline rule rejected a job read or write."""


class JobNotFoundError(JobStateError):
    """No state exists for the given job ID."""


class CorruptJobStateError(JobStateError):
    """Stored state could not be parsed into a valid JobState."""


# One linear pipeline. The parse worker owns queued -> parsing -> parsed.
# The extract worker owns parsed -> extracting -> done. `failed` records the
# status it replaced in `failed_stage`. `parsed` can fail when the extract queue
# gives up. A delivery that is skipped does not mark it failed, so a later
# wake-up can still claim it.
JobStatus = Literal["queued", "parsing", "parsed", "extracting", "done", "failed"]
FailedStage = Literal["queued", "parsing", "parsed", "extracting"]
# The service a job is waiting for while its stage cannot claim it: MinerU for the
# parser, vLLM for the extractor. A stage sets it only for a status it owns.
WaitingFor = Literal["parser", "extractor"]
WAITING_STATUSES: Mapping[WaitingFor, frozenset[JobStatus]] = MappingProxyType(
    {
        "parser": frozenset({"queued", "parsing"}),
        "extractor": frozenset({"parsed", "extracting"}),
    }
)

# Every legal status change, keyed by the stored status. A store must refuse any write
# whose status is not listed for the stored one, so a stale or duplicate writer cannot
# undo or rewrite progress. Self-loops let a retried attempt report its own status again.
# `done` and `failed` are final: nothing may follow them. This mirrors the state diagram
# in docs/architecture.md.
ALLOWED_TRANSITIONS: Mapping[JobStatus, frozenset[JobStatus]] = MappingProxyType(
    {
        "queued": frozenset({"parsing", "failed"}),
        "parsing": frozenset({"parsing", "parsed", "failed"}),
        "parsed": frozenset({"extracting", "failed"}),
        "extracting": frozenset({"extracting", "done", "failed"}),
        "done": frozenset(),
        "failed": frozenset(),
    }
)
SafeErrorCode = Literal[
    "invalid_pdf",
    "parse_failed",
    "extraction_failed",
    "timeout",
    "deferral_expired",
    "retries_exhausted",
    "internal_error",
]

# Schema version for the JobState contract. Bump the version up when the shape of JobState
# changes in a way that is not backward compatible, so a stored record from
# an older version is recognized as such rather than silently misread.
JOB_STATE_SCHEMA_VERSION: Literal[2] = 2


class SafeError(BaseModel):
    """A short, non-sensitive description of why a job failed."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    code: SafeErrorCode


class JobState(BaseModel):
    """Typed, versioned state for a single parse/extract job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = JOB_STATE_SCHEMA_VERSION
    job_id: UUID4
    status: JobStatus
    parser: str = Field(min_length=1)
    extractor: str = Field(min_length=1)
    document_checksum: Sha256Hex
    parse_result_ref: ArtifactPath | None = None
    extract_result_ref: ArtifactPath | None = None
    error_code: SafeErrorCode | None = None
    # The status this job had when it was marked failed. Empty for every other status.
    failed_stage: FailedStage | None = None
    # Set when the stage's service did not answer and the job was deferred. Both are
    # empty again once the job is claimed or failed. `waiting_since` is the first
    # deferral of this wait.
    waiting_for: WaitingFor | None = None
    waiting_since: AwareDatetime | None = None
    parsing_claims: int = Field(default=0, ge=0)
    extracting_claims: int = Field(default=0, ge=0)
    created_at: AwareDatetime

    def claims(self, status: JobStatus) -> int:
        """How many times this stage has been claimed. Other statuses are not claims."""
        if status == "parsing":
            return self.parsing_claims
        if status == "extracting":
            return self.extracting_claims
        return 0

    def with_changes(self, **changes: object) -> Self:
        """Return a new, validated JobState with the given fields changed.
        Use instead of model_copy(update=...), which skips validation
        entirely and can silently produce a JobState that violates
        _fields_match_status.
        """
        return self.model_validate(self.model_dump() | changes)

    @field_validator("parser")
    @classmethod
    def known_parser(cls, value: str) -> str:
        if value not in PARSER_BACKENDS:
            raise ValueError(f"must be one of: {', '.join(PARSER_BACKENDS)}")
        return value

    @field_validator("extractor")
    @classmethod
    def known_extractor(cls, value: str) -> str:
        if value not in EXTRACTORS:
            raise ValueError(f"must be one of: {', '.join(EXTRACTORS)}")
        return value

    @model_validator(mode="after")
    def _waiting_matches_status(self) -> Self:
        if (self.waiting_for is None) != (self.waiting_since is None):
            raise ValueError(
                "waiting_for and waiting_since must be set together, "
                f"got waiting_for={self.waiting_for!r}, waiting_since={self.waiting_since!r}"
            )
        if self.waiting_for is not None and self.status not in WAITING_STATUSES[self.waiting_for]:
            raise ValueError(f"status={self.status!r} cannot be waiting for the {self.waiting_for}")
        return self

    @model_validator(mode="after")
    def _fields_match_status(self) -> Self:
        has_parse = self.parse_result_ref is not None
        has_extract = self.extract_result_ref is not None
        has_error = self.error_code is not None
        stage = self.failed_stage
        # The parser artifact is stored when the job becomes `parsed`, and failure does not
        # clear it. So a failed job kept that artifact exactly when it failed at `parsed`
        # or `extracting`. The extraction artifact is only recorded on `done`.
        kept = stage in ("parsed", "extracting")
        if self.status == "failed":
            ok = has_error and not has_extract and stage is not None and has_parse is kept
            required = (
                "an error, no extract_result_ref, and a failed_stage that matches "
                "whether the parser artifact was stored"
            )
        elif stage is not None:
            ok = False
            required = "no failed_stage"
        elif self.status in ("parsed", "extracting"):
            ok = has_parse and not has_extract and not has_error
            required = "a parse_result_ref, no extract_result_ref, and no error"
        elif self.status == "done":
            ok = has_parse and has_extract and not has_error
            required = "both artifact refs and no error"
        else:
            ok = not has_parse and not has_extract and not has_error
            required = "no artifact refs and no error"
        if ok:
            return self
        raise ValueError(
            f"status={self.status!r} requires {required}, "
            f"got parse_result_ref={self.parse_result_ref!r}, "
            f"extract_result_ref={self.extract_result_ref!r}, error_code={self.error_code!r}, "
            f"failed_stage={self.failed_stage!r}"
        )
