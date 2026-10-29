"""Document rows on the shared Postgres connection."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, Text, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.orm import Mapped, mapped_column

from bioparser.db.base import Base


class Document(Base):
    """One row of the documents table."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("length(checksum) = 64", name="documents_checksum_length_check"),
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

    checksum: str
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


async def get(connection: AsyncConnection, checksum: str) -> DocumentRecord | None:
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
    checksum: str,
    display_name: str | None,
    size_bytes: int,
) -> tuple[DocumentRecord, bool]:
    """Insert one document if it does not exist, or return the existing document.

    Returns:
        (record, created): created is True if a new row was inserted, False if it already existed.
    """
    existing = await get(connection, checksum)
    if existing is not None:
        return existing, False

    await connection.execute(
        insert(Document).values(
            checksum=checksum,
            display_name=display_name,
            size_bytes=size_bytes,
        )
    )
    inserted = await get(connection, checksum)
    if inserted is None:
        raise RuntimeError(f"Document {checksum} could not be retrieved after insert")
    return inserted, True


async def list_documents(connection: AsyncConnection) -> list[DocumentRecord]:
    """List all stored documents, ordered by creation time descending."""
    rows = (
        (await connection.execute(select(Document.__table__).order_by(Document.created_at.desc())))
        .mappings()
        .all()
    )
    return [_row_to_document_record(cast(Mapping[str, object], r)) for r in rows]
