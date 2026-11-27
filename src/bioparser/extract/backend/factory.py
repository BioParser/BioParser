from __future__ import annotations

from bioparser.extract.backend.default.extractor import DefaultExtractor
from bioparser.extract.backend.mendel.extractor import MendelExtractor
from bioparser.extract.errors import ExtractorBackendUnavailableError
from bioparser.extract.protocol import Extractor
from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME, EXTRACTORS, MENDEL_EXTRACTOR_NAME
from bioparser.services.vllm import VLLMService


def get_extractor(
    name: str,
    *,
    vllm: VLLMService,
) -> Extractor:
    """Return the extractor registered under `name`."""
    if name == DEFAULT_EXTRACTOR_NAME:
        return DefaultExtractor(vllm=vllm)
    if name == MENDEL_EXTRACTOR_NAME:
        return MendelExtractor(vllm=vllm)
    raise ExtractorBackendUnavailableError(
        f"Unknown extractor {name!r}. Choose one of: {', '.join(EXTRACTORS)}."
    )
