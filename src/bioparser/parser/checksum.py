from __future__ import annotations


def block_id(checksum: str, page_number: int, order: int) -> str:
    """Stable ID from PDF bytes, page, and block order."""
    return f"{checksum[:12]}-p{page_number}-b{order:04d}"
