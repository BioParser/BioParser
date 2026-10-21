from __future__ import annotations

from typing import Protocol

from bioparser.extract.models import Observation
from bioparser.parser.models import ParserArtifact


class Extractor(Protocol):
    """Shared contract for extractors that read a parser artifact."""

    async def extract(self, artifact: ParserArtifact) -> list[Observation]: ...
