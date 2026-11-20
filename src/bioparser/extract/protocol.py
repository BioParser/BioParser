from __future__ import annotations

from typing import Protocol

from bioparser.extract.models import ExtractionArtifact
from bioparser.parser.models import ParserArtifact


class Extractor(Protocol):
    """Shared contract for extractors that produce an `ExtractionArtifact`."""

    @property
    def configuration(self) -> str | None: ...

    async def extract(self, artifact: ParserArtifact) -> ExtractionArtifact: ...
