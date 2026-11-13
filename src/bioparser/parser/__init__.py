"""Parser types and helpers."""

from bioparser.parser.errors import (
    ParserBackendUnavailableError,
    ParserProcessError,
    ParserTimeoutError,
    UnsupportedDocumentError,
)
from bioparser.parser.models import ParserArtifact
from bioparser.parser.pdf_bytes import parse_pdf_bytes
from bioparser.parser.protocol import PdfParser
from bioparser.parser_names import DEFAULT_PARSER_NAME, MINERU_PARSER_NAME, PARSER_BACKENDS

__all__ = [
    "DEFAULT_PARSER_NAME",
    "MINERU_PARSER_NAME",
    "PARSER_BACKENDS",
    "ParserArtifact",
    "ParserBackendUnavailableError",
    "ParserProcessError",
    "ParserTimeoutError",
    "PdfParser",
    "UnsupportedDocumentError",
    "parse_pdf_bytes",
]
