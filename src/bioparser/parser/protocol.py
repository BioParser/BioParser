from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Protocol

from bioparser.parser.models import ParserArtifact


class PdfParser(Protocol):
    """Shared contract for PDF backends that produce a `ParserArtifact`."""

    def parse(self, path: Path) -> ParserArtifact: ...


def parse_pdf_bytes(parser: PdfParser, pdf: bytes) -> ParserArtifact:
    """Parse PDF bytes with a backend that reads a path. The temporary file is removed after."""
    # Windows refuses to open a file by name while another handle has it open (#219), so the
    # handle is closed before the parse. delete_on_close=False keeps the file until the
    # `with` block ends.
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete_on_close=False) as handle:
        handle.write(pdf)
        handle.close()
        return parser.parse(Path(handle.name))
