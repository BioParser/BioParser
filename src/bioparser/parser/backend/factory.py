from __future__ import annotations

import os

from bioparser.parser.backend.default.parser import DefaultParser
from bioparser.parser.backend.mineru.parser import MinerUParser
from bioparser.parser.errors import ParserBackendUnavailableError
from bioparser.parser.protocol import PdfParser
from bioparser.parser_names import DEFAULT_PARSER_NAME, MINERU_PARSER_NAME, PARSER_BACKENDS
from bioparser.services.mineru.client import DEFAULT_TIER, MINERU_TIERS, MinerUTier

# Where the host scripts find mineru-api: a container run on loopback (docs/parser.md).
LOCAL_MINERU_BASE_URL = "http://127.0.0.1:8001"


def get_parser(
    name: str,
    *,
    timeout_s: float | None = None,
    mineru_base_url: str | None = None,
    mineru_tier: MinerUTier | None = None,
) -> PdfParser:
    """Return a parser instance for a backend name.

    `timeout_s` limits a parse in the MinerU service. The default backend runs in-process
    and ignores it. Without `mineru_base_url` and `mineru_tier`, MinerU is reached at
    $BIOPARSER_MINERU_BASE_URL (else LOCAL_MINERU_BASE_URL) and asked for
    $BIOPARSER_MINERU_TIER (else DEFAULT_TIER).
    """
    if name == DEFAULT_PARSER_NAME:
        return DefaultParser()
    if name == MINERU_PARSER_NAME:
        base_url = mineru_base_url or os.environ.get(
            "BIOPARSER_MINERU_BASE_URL", LOCAL_MINERU_BASE_URL
        )
        tier = mineru_tier or os.environ.get("BIOPARSER_MINERU_TIER", DEFAULT_TIER)
        if tier not in MINERU_TIERS:
            raise ParserBackendUnavailableError(
                f"Unknown MinerU tier {tier!r}. Choose one of: {', '.join(MINERU_TIERS)}."
            )
        return MinerUParser(base_url, timeout_s, tier=tier)
    raise ParserBackendUnavailableError(
        f"Unknown parser backend {name!r}. Choose one of: {', '.join(PARSER_BACKENDS)}."
    )
