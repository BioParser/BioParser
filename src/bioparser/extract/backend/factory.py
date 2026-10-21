from __future__ import annotations

from bioparser.extract.backend.default.extractor import DefaultExtractor
from bioparser.extract.errors import ExtractorBackendUnavailableError
from bioparser.extract.protocol import Extractor
from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME, EXTRACTORS
from bioparser.services.vllm import VLLMService


def get_extractor(
    name: str,
    *,
    vllm: VLLMService,
    char_budget: int,
    max_tokens: int,
) -> Extractor:
    """Return the extractor registered under `name`."""
    if name == DEFAULT_EXTRACTOR_NAME:
        return DefaultExtractor(
            vllm=vllm,
            char_budget=char_budget,
            max_tokens=max_tokens,
        )
    raise ExtractorBackendUnavailableError(
        f"Unknown extractor {name!r}. Choose one of: {', '.join(EXTRACTORS)}."
    )
