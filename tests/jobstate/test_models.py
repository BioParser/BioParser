import pytest
from pydantic import ValidationError

from bioparser.jobstate import JOB_STATE_SCHEMA_VERSION, JobState, SafeError


def _queued(**overrides: object) -> JobState:
    fields: dict[str, object] = {
        "job_id": "11111111-1111-1111-1111-111111111111",
        "document_id": "a" * 64,  # 64 hex chars, valid sha256
        "status": "queued",
        "input_artifact_ref": "artifacts/doc-1/input.pdf",
    }
    fields.update(overrides)
    return JobState.model_validate(fields)


# --- round trips -------------------------------------------------------


def test_queued_job_round_trips_through_json() -> None:
    state = _queued()
    restored = JobState.model_validate_json(state.model_dump_json())
    assert restored == state
    assert restored.schema_version == JOB_STATE_SCHEMA_VERSION


def test_running_job_round_trips_through_json() -> None:
    state = _queued(status="running")
    restored = JobState.model_validate_json(state.model_dump_json())
    assert restored == state


def test_succeeded_job_round_trips_through_json() -> None:
    state = _queued(status="succeeded", output_artifact_ref="artifacts/doc-1/output.json")
    restored = JobState.model_validate_json(state.model_dump_json())
    assert restored == state
    assert restored.output_artifact_ref == "artifacts/doc-1/output.json"


def test_failed_job_round_trips_through_json() -> None:
    state = _queued(status="failed", error=SafeError(code="parse_failed"))
    restored = JobState.model_validate_json(state.model_dump_json())
    assert restored == state
    assert restored.error is not None
    assert restored.error.code == "parse_failed"


# --- status/field invariants --------------------------------------------


def test_succeeded_job_requires_output_artifact() -> None:
    with pytest.raises(ValidationError):
        _queued(status="succeeded")


def test_succeeded_job_rejects_error() -> None:
    with pytest.raises(ValidationError):
        _queued(
            status="succeeded",
            output_artifact_ref="artifacts/doc-1/output.json",
            error=SafeError(code="parse_failed"),
        )


def test_failed_job_requires_error() -> None:
    with pytest.raises(ValidationError):
        _queued(status="failed")


def test_failed_job_rejects_output_artifact() -> None:
    with pytest.raises(ValidationError):
        _queued(
            status="failed",
            output_artifact_ref="artifacts/doc-1/output.json",
            error=SafeError(code="parse_failed"),
        )


@pytest.mark.parametrize("status", ["queued", "running"])
def test_non_terminal_job_rejects_output_artifact(status: str) -> None:
    with pytest.raises(ValidationError):
        _queued(status=status, output_artifact_ref="artifacts/doc-1/output.json")


@pytest.mark.parametrize("status", ["queued", "running"])
def test_non_terminal_job_rejects_error(status: str) -> None:
    with pytest.raises(ValidationError):
        _queued(status=status, error=SafeError(code="parse_failed"))


def test_with_changes_returns_new_validated_instance() -> None:
    state = _queued()
    running = state.with_changes(status="running")
    assert running.status == "running"
    assert running is not state
    assert state.status == "queued"  # original is untouched (frozen)


def test_with_changes_rejects_invalid_combination() -> None:
    state = _queued()
    # queued -> succeeded with no output_artifact_ref should be rejected,
    # same as constructing it directly would be.
    with pytest.raises(ValidationError):
        state.with_changes(status="succeeded")


# --- Immutability and schema version --------------------------------------


def test_job_state_is_frozen() -> None:
    state = _queued()
    with pytest.raises(ValidationError):
        state.status = "running"


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


def test_job_id_must_look_like_a_uuid() -> None:
    with pytest.raises(ValidationError):
        _queued(job_id="not-a-uuid")


def test_document_id_must_be_a_sha256_hex_digest() -> None:
    with pytest.raises(ValidationError):
        _queued(document_id="doc-1")
