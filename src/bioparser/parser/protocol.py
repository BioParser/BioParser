from __future__ import annotations

from pathlib import Path
from typing import Protocol

from bioparser.jobs import JobState
from bioparser.parser.models import ParserArtifact


class PdfParser(Protocol):
    """Shared contract for PDF backends that produce a `ParserArtifact`."""

    def artifact_path_segments(self, job: JobState) -> list[str]: ...

    async def parse(self, path: Path) -> ParserArtifact: ...
