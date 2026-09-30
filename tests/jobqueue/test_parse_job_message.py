import pytest
from pydantic import ValidationError

from bioparser.jobqueue import ParseJobMessage


def _valid(**overrides: object) -> ParseJobMessage:
    data: dict[str, object] = {
        "job_id": "job-1",
        "document_id": "doc-1",
        "input_pdf_ref": "pdf-1",
    }
    data.update(overrides)
    return ParseJobMessage.model_validate(data)


def test_round_trip_json() -> None:
    original = _valid(schema_version=1)
    restored = ParseJobMessage.model_validate_json(original.model_dump_json())
    assert restored == original


def test_default_schema_version() -> None:
    assert _valid().schema_version == 1


def test_rejects_empty_fields() -> None:
    for field in ("job_id", "document_id", "input_pdf_ref"):
        with pytest.raises(ValidationError):
            _valid(**{field: ""})


def test_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        _valid(pdf_bytes="nope")


def test_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValidationError):
        _valid(schema_version=2)
