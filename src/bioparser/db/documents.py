"""Document rows on the shared Postgres connection."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, Text, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.orm import Mapped, mapped_column

from bioparser.db.base import Base
from bioparser.hashing import Sha256Hex


class Document(Base):
    """One row of the documents table."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="documents_checksum_hex_check"),
        CheckConstraint("size_bytes >= 0", name="documents_size_bytes_check"),
    )

    checksum: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class DocumentRecord(BaseModel):
    """In-memory representation of a document row."""

    model_config = ConfigDict(frozen=True)

    checksum: Sha256Hex
    display_name: str | None
    size_bytes: int
    created_at: datetime


def _row_to_document_record(row: Mapping[str, object]) -> DocumentRecord:
    return DocumentRecord(
        checksum=str(row["checksum"]),
        display_name=cast(str | None, row["display_name"]),
        size_bytes=int(cast(int, row["size_bytes"])),
        created_at=cast(datetime, row["created_at"]),
    )


async def get(connection: AsyncConnection, checksum: Sha256Hex) -> DocumentRecord | None:
    """Load one document by checksum, or None when missing."""
    row = (
        (await connection.execute(select(Document.__table__).where(Document.checksum == checksum)))
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return _row_to_document_record(cast(Mapping[str, object], row))


async def create(
    connection: AsyncConnection,
    *,
    checksum: Sha256Hex,
    display_name: str | None,
    size_bytes: int,
) -> tuple[DocumentRecord, bool]:
    """Insert one document if it does not exist, or return the existing document.

    Returns:
        (record, created): created is True if a new row was inserted, False if it already existed.
    """
    stmt = (
        pg_insert(Document)
        .values(
            checksum=checksum,
            display_name=display_name,
            size_bytes=size_bytes,
        )
        .on_conflict_do_nothing(index_elements=["checksum"])
    )
    result = await connection.execute(stmt)
    created = result.rowcount == 1
    doc = await get(connection, checksum)
    if doc is None:
        raise RuntimeError(f"Document {checksum} could not be retrieved after insert")
    return doc, created


async def list_documents(connection: AsyncConnection) -> list[DocumentRecord]:
    """List all stored documents, ordered by creation time descending."""
    rows = (
        (await connection.execute(select(Document.__table__).order_by(Document.created_at.desc())))
        .mappings()
        .all()
    )
    return [_row_to_document_record(cast(Mapping[str, object], r)) for r in rows]
