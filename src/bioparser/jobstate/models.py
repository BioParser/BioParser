from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal, Self

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator, model_validator

from bioparser.artifact_paths import ArtifactPath
from bioparser.extractor_names import EXTRACTORS
from bioparser.parser_names import PARSER_BACKENDS

# One linear pipeline. The parse worker owns queued -> parsing -> parsed.
# The extract worker owns parsed -> extracting -> done. `failed` ends the stage
# that is running. `parsed` waits for an extract message: it has no transition
# to `failed`, so a lost wake-up stays retryable.
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
    extractor: str = Field(min_length=1)
    pdf_ref: ArtifactPath
    parse_result_ref: ArtifactPath | None = None
    extract_result_ref: ArtifactPath | None = None
    error_code: SafeErrorCode | None = None
    parsing_claims: int = Field(default=0, ge=0)
    extracting_claims: int = Field(default=0, ge=0)

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
    def _fields_match_status(self) -> Self:
        has_parse = self.parse_result_ref is not None
        has_extract = self.extract_result_ref is not None
        has_error = self.error_code is not None
        # `failed` may keep the parser artifact. Extraction fails after that artifact exists,
        # and dropping the reference would hide a successful parse. The extraction artifact
        # is only recorded on `done`.
        if (
            self.status in ("parsed", "extracting")
            and has_parse
            and not has_extract
            and not has_error
        ):
            return self
        if self.status == "done" and has_parse and has_extract and not has_error:
            return self
        if self.status == "failed" and has_error and not has_extract:
            return self
        if (
            self.status in ("queued", "parsing")
            and not has_parse
            and not has_extract
            and not has_error
        ):
            return self
        if self.status == "failed":
            required = "an error and no extract_result_ref"
        elif self.status in ("queued", "parsing"):
            required = "no artifact refs and no error"
        elif self.status == "done":
            required = "both artifact refs and no error"
        else:
            required = "a parse_result_ref, no extract_result_ref, and no error"
        raise ValueError(
            f"status={self.status!r} requires {required}, "
            f"got parse_result_ref={self.parse_result_ref!r}, "
            f"extract_result_ref={self.extract_result_ref!r}, error_code={self.error_code!r}"
        )
