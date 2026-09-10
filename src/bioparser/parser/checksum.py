from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def block_id(checksum: str, page_number: int, order: int) -> str:
    """Stable ID from PDF bytes, page, and block order."""
    return f"{checksum[:12]}-p{page_number}-b{order:04d}"
