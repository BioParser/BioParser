"""The checksum used to identify artifacts, such as a PDF in its artifact path.

Kept outside `bioparser.parser` so that the API and workers can hash without loading the
parser backends.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated

from pydantic import StringConstraints

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
"""A lowercase hex sha256 digest, as returned by `sha256_bytes` and `sha256_file`."""


def sha256_bytes(data: bytes) -> Sha256Hex:
    """Hex sha256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> Sha256Hex:
    """Hex sha256 digest of the file at ``path``, read in chunks."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()
