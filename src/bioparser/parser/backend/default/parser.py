from __future__ import annotations

import asyncio
import io
import tempfile
from importlib.metadata import version
from pathlib import Path

import pdfplumber
from pdfplumber.page import Page as PdfPlumberPage
from pydantic import ValidationError

from bioparser.hashing import sha256_bytes
from bioparser.jobs import JobState
from bioparser.parser.advent_repair.symbol_child import repair_pdf_in_child
from bioparser.parser.advent_repair.symbol_fonts import repair_pdf, symbol_font_problems
from bioparser.parser.backend.default.child import EXIT_NOT_FOUND, EXIT_UNSUPPORTED
from bioparser.parser.backend.default.grouping import (
    SPAN_WIDTH_FACTOR,
    LineGeom,
    WordBox,
    group_words_into_text_blocks,
)
from bioparser.parser.checksum import block_id
from bioparser.parser.child import run_child
from bioparser.parser.errors import ParserProcessError, UnsupportedDocumentError
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
    TextLine,
)
from bioparser.parser_names import DEFAULT_PARSER_NAME, PARSER_VERSIONS

DEFAULT_PARSER_VERSION = PARSER_VERSIONS[DEFAULT_PARSER_NAME]

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


def _unit(value: float, size: float) -> float:
    if size <= 0:
        return 0.0
    return min(1.0, max(0.0, value / size))


def _text_lines(block_lines: tuple[LineGeom, ...], width: float, height: float) -> list[TextLine]:
    lines: list[TextLine] = []
    for line in block_lines:
        x0: list[float | None] = []
        x1: list[float | None] = []
        for left, right in zip(line.x0, line.x1, strict=True):
            if left is None or right is None:
                x0.append(None)
                x1.append(None)
                continue
            norm_left = _unit(left, width)
            norm_right = _unit(right, width)
            if norm_right < norm_left:
                x0.append(None)
                x1.append(None)
                continue
            x0.append(norm_left)
            x1.append(norm_right)
        y0 = _unit(line.top, height)
        y1 = _unit(line.bottom, height)
        if y1 < y0:
            continue
        lines.append(TextLine(start=line.start, end=line.end, y0=y0, y1=y1, x0=x0, x1=x1))
    return lines


def _words_from_page(page: PdfPlumberPage) -> list[WordBox]:
    working = page.dedupe_chars() if DEDUPE_CHARS else page
    raw_words = working.extract_words(
        x_tolerance=WORD_X_TOLERANCE,
        y_tolerance=WORD_Y_TOLERANCE,
        return_chars=True,
    )
    words: list[WordBox] = []
    for item in raw_words:
        glyphs: list[tuple[str, float, float]] = []
        for char in item.get("chars") or []:
            glyph = str(char.get("text") or "").replace("\u0000", "")
            if not glyph:
                continue
            glyphs.append((glyph, float(char["x0"]), float(char["x1"])))
        # pdfplumber expands ligatures in the word text and not in each glyph.
        # Citations compare that word text, so a drawn "ﬁ" stays "fi" there.
        text = str(item.get("text") or "").replace("\u0000", "")
        if not text:
            continue
        words.append(
            WordBox(
                text=text,
                x0=float(item["x0"]),
                x1=float(item["x1"]),
                top=float(item["top"]),
                bottom=float(item["bottom"]),
                glyphs=tuple(glyphs),
            )
        )
    return words


class DefaultParser:
    """Default digital-born PDF backend. Text currently uses pdfplumber."""

    def artifact_path_segments(self, job: JobState) -> list[str]:
        return [DEFAULT_PARSER_NAME, PARSER_VERSIONS[DEFAULT_PARSER_NAME]]

    async def parse(self, path: Path) -> ParserArtifact:
        """Repair in one child, then parse that PDF in another.

        A crash in the repair does not kill the parse. The parse then reads
        the original PDF, and the artifact records that the fonts were not checked.
        """
        pdf_path = path.expanduser().resolve()
        if not pdf_path.is_file():
            raise FileNotFoundError("PDF not found")
        original = await asyncio.to_thread(pdf_path.read_bytes)
        checksum = sha256_bytes(original)
        repaired = await repair_pdf_in_child(pdf_path, original=original)
        repaired_path = await asyncio.to_thread(_write_pdf, repaired.data)
        try:
            returncode, out = await run_child(
                "bioparser.parser.backend.default.child",
                str(repaired_path),
                checksum,
            )
            artifact = _artifact_from_child(returncode, out)
        finally:
            repaired_path.unlink(missing_ok=True)
        return artifact.model_copy(
            update={
                "problems": symbol_font_problems(repaired),
                "checksum": checksum,
            }
        )

    def parse_in_this_process(
        self, path: Path, *, repair: bool = True, checksum: str | None = None
    ) -> ParserArtifact:
        pdf_path = path.expanduser().resolve()
        if not pdf_path.is_file():
            raise FileNotFoundError("PDF not found")

        original = pdf_path.read_bytes()
        if checksum is None:
            checksum = sha256_bytes(original)
        if repair:
            repaired = repair_pdf(original)
            data = repaired.data
            problems = symbol_font_problems(repaired)
        else:
            data = original
            problems = []
        pages: list[Page] = []
        extracted_any = False

        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for index, page in enumerate(pdf.pages):
                    page_number = index + 1
                    width = float(page.width)
                    height = float(page.height)
                    words = _words_from_page(page)
                    # Frees the page's parsed objects: without it memory grows by about 10 MiB a page.
                    page.close()
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
                                    lines=_text_lines(block.lines, width, height),
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
            problems=problems,
        )


def _write_pdf(data: bytes) -> Path:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(data)
        return Path(handle.name)


def _artifact_from_child(returncode: int | None, out: bytes) -> ParserArtifact:
    text = out.decode(errors="replace")
    if returncode == 0:
        try:
            return ParserArtifact.model_validate_json(out)
        except ValidationError as exc:
            raise ParserProcessError("pdfplumber process returned an invalid artifact") from exc
    if returncode == EXIT_UNSUPPORTED:
        raise UnsupportedDocumentError(text)
    if returncode == EXIT_NOT_FOUND:
        raise FileNotFoundError(text)
    raise ParserProcessError(f"pdfplumber process exited with {returncode}")
