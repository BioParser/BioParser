from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from bioparser.extract.report import ExtractionReport
    from bioparser.parser.models import ParserArtifact


class Extractor(Protocol):
    """Shared contract for extractors that produce an `ExtractionReport`."""

    async def extract(self, artifact: ParserArtifact) -> ExtractionReport: ...
