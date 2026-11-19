from __future__ import annotations

import asyncio
import io
import os
import sys
from importlib.metadata import version
from pathlib import Path

import pdfplumber
from pdfplumber.page import Page as PdfPlumberPage
from pydantic import ValidationError

from bioparser.hashing import sha256_file
from bioparser.parser.backend.default.child import EXIT_NOT_FOUND, EXIT_UNSUPPORTED
from bioparser.parser.backend.default.grouping import (
    SPAN_WIDTH_FACTOR,
    LineGeom,
    WordBox,
    group_words_into_text_blocks,
)
from bioparser.parser.checksum import block_id
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
from bioparser.parser.symbol_fonts import repair_pdf
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
            text = str(char.get("text") or "").replace("\u0000", "")
            if not text:
                continue
            glyphs.append((text, float(char["x0"]), float(char["x1"])))
        text = "".join(glyph for glyph, _left, _right in glyphs)
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


# Names a child needs to start. The child must not inherit the database and Redis
# passwords. This is not a sandbox: code in the child runs as the same user and can
# still read them from /proc/<pid>/environ. #286 limits what a stolen password can do.
_CHILD_ENV = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "PATHEXT", "TEMP", "TMP", "TMPDIR")


def _child_environment() -> dict[str, str]:
    return {name: os.environ[name] for name in _CHILD_ENV if name in os.environ}


class DefaultParser:
    """Default digital-born PDF backend. Text currently uses pdfplumber."""

    async def parse(self, path: Path) -> ParserArtifact:
        """Parse the PDF in a child process."""
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "bioparser.parser.backend.default.child",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=_child_environment(),
        )
        try:
            out, _ = await child.communicate()
        except asyncio.CancelledError:
            if child.returncode is None:
                child.kill()
            await child.wait()
            raise
        return _artifact_from_child(child.returncode, out)

    def parse_in_this_process(self, path: Path) -> ParserArtifact:
        pdf_path = path.expanduser().resolve()
        if not pdf_path.is_file():
            raise FileNotFoundError("PDF not found")

        original = pdf_path.read_bytes()
        checksum = sha256_file(pdf_path)
        repaired, unmapped_symbol_count = repair_pdf(original)
        pages: list[Page] = []
        extracted_any = False

        try:
            with pdfplumber.open(io.BytesIO(repaired)) as pdf:
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
            unmapped_symbol_count=unmapped_symbol_count,
        )


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
