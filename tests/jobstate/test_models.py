import pytest
from pydantic import ValidationError

from bioparser.jobstate import JOB_STATE_SCHEMA_VERSION, JobState, SafeError


def _queued(**overrides: object) -> JobState:
    fields: dict[str, object] = {
        "job_id": "11111111-1111-1111-1111-111111111111",
        "document_id": "doc-1",
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
    state = _queued(status="failed", error=SafeError(message="parser rejected the document"))
    restored = JobState.model_validate_json(state.model_dump_json())
    assert restored == state
    assert restored.error is not None
    assert restored.error.message == "parser rejected the document"


# --- status/field invariants --------------------------------------------


def test_succeeded_job_requires_output_artifact() -> None:
    with pytest.raises(ValidationError):
        _queued(status="succeeded")


def test_succeeded_job_rejects_error() -> None:
    with pytest.raises(ValidationError):
        _queued(
            status="succeeded",
            output_artifact_ref="artifacts/doc-1/output.json",
            error=SafeError(message="boom"),
        )


def test_failed_job_requires_error() -> None:
    with pytest.raises(ValidationError):
        _queued(status="failed")


def test_failed_job_rejects_output_artifact() -> None:
    with pytest.raises(ValidationError):
        _queued(
            status="failed",
            output_artifact_ref="artifacts/doc-1/output.json",
            error=SafeError(message="boom"),
        )


@pytest.mark.parametrize("status", ["queued", "running"])
def test_non_terminal_job_rejects_output_artifact(status: str) -> None:
    with pytest.raises(ValidationError):
        _queued(status=status, output_artifact_ref="artifacts/doc-1/output.json")


@pytest.mark.parametrize("status", ["queued", "running"])
def test_non_terminal_job_rejects_error(status: str) -> None:
    with pytest.raises(ValidationError):
        _queued(status=status, error=SafeError(message="boom"))


# --- SafeError -----------------------------------------------------------


def test_safe_error_rejects_empty_message() -> None:
    with pytest.raises(ValidationError):
        SafeError(message="")


def test_safe_error_rejects_message_over_500_chars() -> None:
    with pytest.raises(ValidationError):
        SafeError(message="x" * 501)


def test_safe_error_accepts_message_at_500_chars() -> None:
    error = SafeError(message="x" * 500)
    assert len(error.message) == 500


# --- immutability and schema version --------------------------------------


def test_job_state_is_frozen() -> None:
    state = _queued()
    with pytest.raises(ValidationError):
        state.status = "running"  # type: ignore[misc]


def test_job_state_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValidationError):
        _queued(schema_version=2)
