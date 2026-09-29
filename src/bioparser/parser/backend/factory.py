from __future__ import annotations

from bioparser.parser.backend.default.parser import DefaultParser
from bioparser.parser.backend.mineru.parser import MinerUParser
from bioparser.parser.errors import ParserBackendUnavailableError
from bioparser.parser.protocol import PdfParser
from bioparser.parser_names import DEFAULT_PARSER_NAME, MINERU_PARSER_NAME, PARSER_BACKENDS


def get_parser(name: str, *, timeout_s: float | None = None) -> PdfParser:
    """Return a parser instance for a backend name.

    `timeout_s` limits an external parser process (MinerU). The default backend
    runs in-process and ignores it.
    """
    if name == DEFAULT_PARSER_NAME:
        return DefaultParser()
    if name == MINERU_PARSER_NAME:
        return MinerUParser(timeout_s)
    raise ParserBackendUnavailableError(
        f"Unknown parser backend {name!r}. Choose one of: {', '.join(PARSER_BACKENDS)}."
    )
