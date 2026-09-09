from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def block_id(checksum: str, page_number: int, order: int) -> str:
    """Stable ID from PDF bytes, page, and block order."""
    return f"{checksum[:12]}-p{page_number}-b{order:04d}"
