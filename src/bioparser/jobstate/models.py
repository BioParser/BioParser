from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal, Self

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator, model_validator

from bioparser.parser_names import PARSER_BACKENDS

# One linear pipeline. The parse worker owns queued -> parsing -> parsed.
# A later extraction step owns parsed -> extracting -> done. `failed` ends the stage
# that is running: only a stage's own worker can fail its job, so `parsed` (a finished
# stage that nobody owns) has no way to `failed`.
JobStatus = Literal["queued", "parsing", "parsed", "extracting", "done", "failed"]

# Every legal status change, keyed by the stored status. A store must refuse any write
# whose status is not listed for the stored one, so a stale or duplicate writer cannot
# undo or rewrite progress. Self-loops let a retried attempt report its own status again.
# `done` and `failed` are final: nothing may follow them. This mirrors the state diagram
# in docs/architecture.md.
ALLOWED_TRANSITIONS: Mapping[JobStatus, frozenset[JobStatus]] = MappingProxyType(
    {
        "queued": frozenset({"parsing", "failed"}),
        "parsing": frozenset({"parsing", "parsed", "failed"}),
        "parsed": frozenset({"extracting"}),
        "extracting": frozenset({"extracting", "done", "failed"}),
        "done": frozenset(),
        "failed": frozenset(),
    }
)
SafeErrorCode = Literal[
    "invalid_pdf", "parse_failed", "extraction_failed", "timeout", "internal_error"
]

# Schema version for the JobState contract. Bump the version up when the shape of JobState
# changes in a way that is not backward compatible, so a stored record from
# an older version is recognized as such rather than silently misread.
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
    status: JobStatus
    parser: str = Field(min_length=1)
    pdf_ref: str = Field(min_length=1)
    parse_result_ref: str | None = Field(default=None, min_length=1)
    error: SafeError | None = None
    # Claims per stage.
    claim_counts: dict[JobStatus, int] = Field(default_factory=dict)

    def claims(self, status: JobStatus) -> int:
        """How many times status has been claimed."""
        return self.claim_counts.get(status, 0)

    def with_claim(self, status: JobStatus) -> Self:
        """Return this job moved to status, counting one start of that stage.

        The count includes this start. A stage that cannot be re-entered is not a claim.
        Store the result before the heavy work, then read the job again before a later write.
        """
        if status not in ALLOWED_TRANSITIONS[status]:
            raise ValueError(f"status={status!r} cannot be claimed")
        counts = dict(self.claim_counts)
        counts[status] = counts.get(status, 0) + 1
        return self.with_changes(status=status, claim_counts=counts)

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

    @field_validator("claim_counts")
    @classmethod
    def claim_counts_are_per_stage(cls, value: dict[JobStatus, int]) -> dict[JobStatus, int]:
        for status, count in value.items():
            if status not in ALLOWED_TRANSITIONS[status]:
                raise ValueError(f"status={status!r} cannot be claimed")
            if count < 0:
                raise ValueError(f"claim count for {status!r} must be >= 0")
        return value

    @model_validator(mode="after")
    def _fields_match_status(self) -> Self:
        has_result = self.parse_result_ref is not None
        has_error = self.error is not None
        if self.status in ("parsed", "extracting", "done") and has_result and not has_error:
            return self
        if self.status == "failed" and has_error and not has_result:
            return self
        if self.status in ("queued", "parsing") and not has_result and not has_error:
            return self
        if self.status == "failed":
            required = "an error and no parse_result_ref"
        elif self.status in ("queued", "parsing"):
            required = "neither a parse_result_ref nor an error"
        else:
            required = "a parse_result_ref and no error"
        raise ValueError(
            f"status={self.status!r} requires {required}, "
            f"got parse_result_ref={self.parse_result_ref!r}, error={self.error!r}"
        )
