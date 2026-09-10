from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import pdfplumber
from pdfplumber.page import Page as PdfPlumberPage

from bioparser.parser.backend.default.grouping import (
    SPAN_WIDTH_FACTOR,
    WordBox,
    group_words_into_text_blocks,
)
from bioparser.parser.checksum import block_id, sha256_file
from bioparser.parser.errors import UnsupportedDocumentError
from bioparser.parser.models import (
    PARSER_ARTIFACT_SCHEMA_VERSION,
    BlockKind,
    BlockRole,
    BoundingBox,
    ContentBlock,
    Page,
    ParserArtifact,
    ParserInfo,
    TextContent,
    TextSpan,
)

DEFAULT_PARSER_NAME = "default"
DEFAULT_PARSER_VERSION = "1"

# Distances below are PDF points (1 pt = 1/72 in) unless noted as a factor.
WORD_X_TOLERANCE = 1.0  # extract_words: join characters this close in x into one word
WORD_Y_TOLERANCE = 3.0  # extract_words: same for y (baseline jitter)
LINE_TOLERANCE = 3.0  # words whose top differs by at most this share a line band
PARAGRAPH_GAP_FACTOR = 0.6  # new block if gap > this times previous line height (unitless)
COLUMN_GUTTER_FACTOR = 1.0  # split a line band when x-gap > this × word height (unitless)
MIN_X_OVERLAP = 8.0  # last-line x overlap at or below this is a different column
DEDUPE_CHARS = True


def _clamp_unit(value: float) -> float:
    return min(1.0, max(0.0, value))


def _normalize_bbox(
    x0: float,
    top: float,
    x1: float,
    bottom: float,
    width: float,
    height: float,
) -> BoundingBox:
    return BoundingBox(
        x0=_clamp_unit(x0 / width),
        y0=_clamp_unit(top / height),
        x1=_clamp_unit(x1 / width),
        y1=_clamp_unit(bottom / height),
    )


def _words_from_page(page: PdfPlumberPage) -> list[WordBox]:
    working = page.dedupe_chars() if DEDUPE_CHARS else page
    raw_words = working.extract_words(
        x_tolerance=WORD_X_TOLERANCE,
        y_tolerance=WORD_Y_TOLERANCE,
    )
    words: list[WordBox] = []
    for item in raw_words:
        words.append(
            WordBox(
                text=str(item["text"]),
                x0=float(item["x0"]),
                x1=float(item["x1"]),
                top=float(item["top"]),
                bottom=float(item["bottom"]),
            )
        )
    return words


class DefaultParser:
    """Default digital-born PDF backend. Text currently uses pdfplumber."""

    def parse(self, path: Path) -> ParserArtifact:
        pdf_path = path.expanduser().resolve()
        if not pdf_path.is_file():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        checksum = sha256_file(pdf_path)
        pages: list[Page] = []
        extracted_any = False

        try:
            with pdfplumber.open(pdf_path) as pdf:
                for index, page in enumerate(pdf.pages):
                    page_number = index + 1
                    width = float(page.width)
                    height = float(page.height)
                    words = _words_from_page(page)
                    blocks_out: list[ContentBlock] = []
                    grouped = group_words_into_text_blocks(
                        words,
                        line_tolerance=LINE_TOLERANCE,
                        paragraph_gap_factor=PARAGRAPH_GAP_FACTOR,
                        column_gutter_factor=COLUMN_GUTTER_FACTOR,
                        min_x_overlap=MIN_X_OVERLAP,
                        page_width=width,
                    )
                    if grouped:
                        extracted_any = True
                    for order, block in enumerate(grouped):
                        blocks_out.append(
                            ContentBlock(
                                block_id=block_id(checksum, page_number, order),
                                order=order,
                                kind=BlockKind.TEXT,
                                role=BlockRole.BODY,
                                content=TextContent(
                                    text=block.text,
                                    spans=[
                                        TextSpan(
                                            start=span.start,
                                            end=span.end,
                                            line=span.line,
                                            bbox=_normalize_bbox(
                                                span.x0,
                                                span.top,
                                                span.x1,
                                                span.bottom,
                                                width,
                                                height,
                                            ),
                                        )
                                        for span in block.spans
                                    ],
                                ),
                                bbox=_normalize_bbox(
                                    block.x0,
                                    block.top,
                                    block.x1,
                                    block.bottom,
                                    width,
                                    height,
                                ),
                            )
                        )
                    pages.append(
                        Page(
                            page_number=page_number,
                            width=width,
                            height=height,
                            blocks=blocks_out,
                        )
                    )
        except Exception as exc:
            raise UnsupportedDocumentError(
                "Failed to parse the PDF; it may be malformed or otherwise unreadable."
            ) from exc

        if not pages or not extracted_any:
            raise UnsupportedDocumentError("Failed to parse any content from the PDF.")

        return ParserArtifact(
            schema_version=PARSER_ARTIFACT_SCHEMA_VERSION,
            checksum=checksum,
            parser=ParserInfo(
                name=DEFAULT_PARSER_NAME,
                version=DEFAULT_PARSER_VERSION,
                configuration={
                    "text": "pdfplumber",
                    "pdfplumber_version": version("pdfplumber"),
                    "x_tolerance": WORD_X_TOLERANCE,
                    "y_tolerance": WORD_Y_TOLERANCE,
                    "line_tolerance": LINE_TOLERANCE,
                    "paragraph_gap_factor": PARAGRAPH_GAP_FACTOR,
                    "column_gutter_factor": COLUMN_GUTTER_FACTOR,
                    "min_x_overlap": MIN_X_OVERLAP,
                    "span_width_factor": SPAN_WIDTH_FACTOR,
                    "dedupe_chars": DEDUPE_CHARS,
                },
            ),
            pages=pages,
        )
