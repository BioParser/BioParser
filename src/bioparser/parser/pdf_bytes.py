"""Parse PDF bytes with a backend that reads a path."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from bioparser.parser.models import ParserArtifact
from bioparser.parser.protocol import PdfParser


def _write_temporary_pdf(pdf: bytes) -> Path:
    # Windows refuses to open a file by name while another handle has it open (#219), so the
    # handle is closed before the parse reads the file.
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(pdf)
    return Path(handle.name)


async def parse_pdf_bytes(parser: PdfParser, pdf: bytes) -> ParserArtifact:
    """Parse PDF bytes with a backend that reads a path. The temporary file is removed after."""
    path = await asyncio.to_thread(_write_temporary_pdf, pdf)
    try:
        return await parser.parse(path)
    finally:
        await asyncio.to_thread(path.unlink, missing_ok=True)
