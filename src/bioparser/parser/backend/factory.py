from __future__ import annotations

from bioparser.parser.backend.default.parser import DEFAULT_PARSER_NAME, DefaultParser
from bioparser.parser.errors import ParserBackendUnavailableError
from bioparser.parser.protocol import PdfParser

PARSER_BACKENDS = (DEFAULT_PARSER_NAME,)


def get_parser(name: str) -> PdfParser:
    """Return a parser instance for a backend name."""
    if name == DEFAULT_PARSER_NAME:
        return DefaultParser()
    raise ParserBackendUnavailableError(
        f"Unknown parser backend {name!r}. Choose one of: {', '.join(PARSER_BACKENDS)}."
    )
