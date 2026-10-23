from __future__ import annotations

from pathlib import Path
from typing import Protocol

from bioparser.parser.models import ParserArtifact


class PdfParser(Protocol):
    """Shared contract for PDF backends that produce a `ParserArtifact`."""

    def parse(self, path: Path) -> ParserArtifact: ...
