"""Document query coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection

from bioparser.db.documents import (
    Document,
    DocumentRecord,
    _row_to_document_record,
    get_display_names,
)


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


@pytest.mark.anyio
async def test_get_display_names_returns_requested_documents() -> None:
    connection = AsyncMock(spec=AsyncConnection)
    result = MagicMock()
    result.all.return_value = [("a" * 64, "sample.pdf"), ("b" * 64, None)]
    connection.execute.return_value = result

    names = await get_display_names(connection, {"a" * 64, "b" * 64, "c" * 64})

    assert names == {"a" * 64: "sample.pdf", "b" * 64: None}
    connection.execute.assert_awaited_once()


@pytest.mark.anyio
async def test_get_display_names_skips_the_query_for_no_checksums() -> None:
    connection = AsyncMock(spec=AsyncConnection)

    assert await get_display_names(connection, set()) == {}
    connection.execute.assert_not_awaited()
