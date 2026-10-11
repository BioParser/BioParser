from typing import get_args

import pytest
from pydantic import ValidationError

from bioparser.jobstate import JOB_STATE_SCHEMA_VERSION, JobState, SafeError
from bioparser.jobstate.models import ALLOWED_TRANSITIONS, JobStatus

PDF_REF = "6b1f0c2a-7d44-4e1a-9c3b-2f8e5a0d6c11"
RESULT_REF = "7c2e1d3b-8e55-4f2b-ad4c-3a9f6b1e7d22"


def _queued(**overrides: object) -> JobState:
    fields: dict[str, object] = {
        "job_id": "3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e34",
        "status": "queued",
        "parser": "default",
        "pdf_ref": PDF_REF,
    }
    fields.update(overrides)
    return JobState.model_validate(fields)


# --- round trips -------------------------------------------------------


def test_claim_counts_a_start_of_that_stage_only() -> None:
    claimed = _queued().with_claim("parsing")
    assert claimed.status == "parsing"
    assert claimed.claim_counts == {"parsing": 1}
    again = _queued(status="parsing", claim_counts={"parsing": 1, "extracting": 2}).with_claim(
        "parsing"
    )
    assert again.claim_counts == {"parsing": 2, "extracting": 2}


def test_a_stage_that_cannot_be_reentered_is_not_a_claim() -> None:
    with pytest.raises(ValueError, match="cannot be claimed"):
        _queued().with_claim("parsed")


def test_stored_state_without_claim_counts_reads_as_empty() -> None:
    raw = _queued().model_dump()
    del raw["claim_counts"]
    restored = JobState.model_validate(raw)
    assert restored.claims("parsing") == 0
    assert restored.claims("extracting") == 0


def test_every_status_has_a_transition_row() -> None:
    """The Redis transition test only walks this table, so a missing status would never run."""
    assert set(ALLOWED_TRANSITIONS) == set(get_args(JobStatus))


def test_queued_job_round_trips_through_json() -> None:
    state = _queued()
    restored = JobState.model_validate_json(state.model_dump_json())
    assert restored == state
    assert restored.schema_version == JOB_STATE_SCHEMA_VERSION


def test_running_job_round_trips_through_json() -> None:
    state = _queued(status="parsing")
    restored = JobState.model_validate_json(state.model_dump_json())
    assert restored == state


def test_succeeded_job_round_trips_through_json() -> None:
    state = _queued(status="parsed", parse_result_ref=RESULT_REF)
    restored = JobState.model_validate_json(state.model_dump_json())
    assert restored == state
    assert restored.parse_result_ref == RESULT_REF


@pytest.mark.parametrize("status", ["extracting", "done"])
def test_later_stages_require_parse_result_and_reject_error(status: str) -> None:
    state = _queued(status=status, parse_result_ref=RESULT_REF)
    assert JobState.model_validate_json(state.model_dump_json()) == state
    with pytest.raises(ValidationError):
        _queued(status=status)
    with pytest.raises(ValidationError):
        _queued(status=status, parse_result_ref=RESULT_REF, error=SafeError(code="parse_failed"))


def test_failed_job_round_trips_through_json() -> None:
    state = _queued(status="failed", error=SafeError(code="parse_failed"))
    restored = JobState.model_validate_json(state.model_dump_json())
    assert restored == state
    assert restored.error is not None
    assert restored.error.code == "parse_failed"


# --- status/field invariants --------------------------------------------


def test_succeeded_job_requires_parse_result() -> None:
    with pytest.raises(ValidationError):
        _queued(status="parsed")


def test_succeeded_job_rejects_error() -> None:
    with pytest.raises(ValidationError):
        _queued(
            status="parsed",
            parse_result_ref=RESULT_REF,
            error=SafeError(code="parse_failed"),
        )


def test_failed_job_requires_error() -> None:
    with pytest.raises(ValidationError):
        _queued(status="failed")


def test_failed_job_rejects_parse_result() -> None:
    with pytest.raises(ValidationError):
        _queued(
            status="failed",
            parse_result_ref=RESULT_REF,
            error=SafeError(code="parse_failed"),
        )


@pytest.mark.parametrize("status", ["queued", "parsing"])
def test_non_terminal_job_rejects_parse_result(status: str) -> None:
    with pytest.raises(ValidationError):
        _queued(status=status, parse_result_ref=RESULT_REF)


@pytest.mark.parametrize("status", ["queued", "parsing"])
def test_non_terminal_job_rejects_error(status: str) -> None:
    with pytest.raises(ValidationError):
        _queued(status=status, error=SafeError(code="parse_failed"))


def test_with_changes_returns_new_validated_instance() -> None:
    state = _queued()
    running = state.with_changes(status="parsing")
    assert running.status == "parsing"
    assert running is not state
    assert state.status == "queued"  # original is untouched (frozen)


def test_with_changes_rejects_invalid_combination() -> None:
    state = _queued()
    # queued -> parsed with no parse_result_ref should be rejected,
    # same as constructing it directly would be.
    with pytest.raises(ValidationError):
        state.with_changes(status="parsed")


# --- Immutability and schema version --------------------------------------


def test_job_state_is_frozen() -> None:
    state = _queued()
    with pytest.raises(ValidationError):
        state.status = "parsing"


def test_job_state_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValidationError):
        _queued(schema_version=2)


# --- Jobstate fields --------------------------------------------------------


def test_job_state_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        _queued(unexpected_field="surprise")


def test_safe_error_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        SafeError.model_validate({"code": "parse_failed", "extra": "surprise"})


def test_job_id_must_be_uuid4() -> None:
    with pytest.raises(ValidationError):
        _queued(job_id="not-a-uuid")
    with pytest.raises(ValidationError):
        _queued(job_id="3f2b8c1e-9a4d-5f6b-8c2e-1d5a7b9c0e34")


def test_artifact_refs_accept_artifact_paths() -> None:
    state = _queued(
        status="parsed",
        pdf_ref="artifacts/doc-1/input.pdf",
        parse_result_ref="artifacts/doc-1/parsed.json",
    )
    assert state.pdf_ref == "artifacts/doc-1/input.pdf"
    assert state.parse_result_ref == "artifacts/doc-1/parsed.json"


@pytest.mark.parametrize("path", ["", "/abs", "a//b", "a/../b", "has space"])
def test_malformed_pdf_ref_is_rejected(path: str) -> None:
    with pytest.raises(ValidationError):
        _queued(pdf_ref=path)


@pytest.mark.parametrize("path", ["", "/abs", "a//b", "a/../b", "has space"])
def test_malformed_parse_result_ref_is_rejected(path: str) -> None:
    with pytest.raises(ValidationError):
        _queued(status="parsed", parse_result_ref=path)


def test_stored_uuid_refs_stay_readable() -> None:
    """Records written before the type change hold UUID strings. They must still load."""
    raw = _queued(status="parsed", parse_result_ref=RESULT_REF).model_dump_json()
    restored = JobState.model_validate_json(raw)
    assert restored.pdf_ref == PDF_REF
    assert restored.parse_result_ref == RESULT_REF


def test_parser_name_is_stored_as_given() -> None:
    assert _queued(parser="mineru").parser == "mineru"


def test_unknown_parser_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _queued(parser="missing")
