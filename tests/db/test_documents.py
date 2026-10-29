"""Document query coverage."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from bioparser.db.documents import Document, DocumentRecord, _row_to_document_record


def test_row_to_document_record() -> None:
    now = datetime.now(UTC)
    row = {
        "checksum": "a" * 64,
        "display_name": "sample.pdf",
        "size_bytes": 1024,
        "created_at": now,
    }
    record = _row_to_document_record(row)
    assert record.checksum == "a" * 64
    assert record.display_name == "sample.pdf"
    assert record.size_bytes == 1024
    assert record.created_at == now


def test_document_model_tablename() -> None:
    assert Document.__tablename__ == "documents"
    assert len(Document.__table__.columns) == 4
    assert Document.__table__.c.checksum.primary_key is True


def test_document_record_frozen() -> None:
    record = DocumentRecord(
        checksum="b" * 64,
        display_name=None,
        size_bytes=500,
        created_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        record.checksum = "c" * 64
