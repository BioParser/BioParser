"""Place MinerU's glyph edges onto a block's stored text.

The glyph string and the stored text are aligned as a diff. Characters that still
agree keep the measured box. A rewritten span shares one box, the union of the ink
that did not match, whatever lines that ink was drawn on. An inline formula often
has no characters left, only the box MinerU measured for it, and that box covers
the formula text. Inserted text
with no glyphs and no box stays empty. A glyph with no stored character is dropped.
A glyph whose text is longer than one character shares that glyph's box evenly.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, NamedTuple

from bioparser.parser.models import BoundingBox, TextLine


def _expand(glyphs: object) -> list[tuple[str, float, float]]:
    """One edge pair per character. A run longer than one character shares its box evenly."""
    if not isinstance(glyphs, list):
        return []
    edges: list[tuple[str, float, float]] = []
    for glyph in glyphs:
        if not isinstance(glyph, dict):
            continue
        text = glyph.get("t")
        left = glyph.get("x0")
        right = glyph.get("x1")
        if (
            not isinstance(text, str)
            or not text
            or isinstance(left, bool)
            or isinstance(right, bool)
            or not isinstance(left, (int, float))
            or not isinstance(right, (int, float))
        ):
            continue
        x0 = float(left)
        x1 = float(right)
        if x1 < x0:
            continue
        if len(text) == 1:
            edges.append((text, x0, x1))
            continue
        step = (x1 - x0) / len(text)
        edges.extend(
            (char, x0 + step * index, x0 + step * (index + 1)) for index, char in enumerate(text)
        )
    return edges


def _unit(value: float) -> float:
    return min(1.0, max(0.0, value))


class _Glyph(NamedTuple):
    char: str
    x0: float
    x1: float
    line_index: int
    y0: float
    y1: float


class _Region(NamedTuple):
    """One formula span: its box, and the LaTeX it contributes to the stored text."""

    anchor: int
    glyph: _Glyph
    latex: str


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return _unit(float(value))


def _collect(raw_lines: list[object]) -> tuple[list[_Glyph], list[_Region]]:
    glyphs: list[_Glyph] = []
    regions: list[_Region] = []
    serial = 0
    for line_index, raw in enumerate(raw_lines):
        if not isinstance(raw, dict):
            continue
        y0 = raw.get("y0")
        y1 = raw.get("y1")
        if (
            isinstance(y0, bool)
            or isinstance(y1, bool)
            or not isinstance(y0, (int, float))
            or not isinstance(y1, (int, float))
        ):
            continue
        top = _unit(float(y0))
        bottom = _unit(float(y1))
        if bottom < top:
            continue
        raw_glyphs = raw.get("glyphs")
        if not isinstance(raw_glyphs, list):
            continue
        for item in raw_glyphs:
            if not isinstance(item, dict):
                continue
            text = item.get("t")
            if text == "":
                left = _number(item.get("x0"))
                right = _number(item.get("x1"))
                if left is None or right is None or right < left:
                    continue
                region_top = _number(item.get("y0"))
                region_bottom = _number(item.get("y1"))
                if region_top is None:
                    region_top = top
                if region_bottom is None:
                    region_bottom = bottom
                if region_bottom < region_top:
                    continue
                serial += 1
                latex = item.get("latex")
                regions.append(
                    _Region(
                        len(glyphs),
                        _Glyph("", left, right, -serial, region_top, region_bottom),
                        latex if isinstance(latex, str) else "",
                    )
                )
                continue
            for char, edge_left, edge_right in _expand([item]):
                if char in "\r\n":
                    continue
                clamped_left = _unit(edge_left)
                clamped_right = _unit(edge_right)
                if clamped_right < clamped_left:
                    continue
                glyphs.append(_Glyph(char, clamped_left, clamped_right, line_index, top, bottom))
    return glyphs, regions


def _cover_gap(
    glyphs: list[_Glyph],
    start: int,
    end: int,
    placed: dict[int, tuple[int, float, float, float, float]],
) -> None:
    """Every character of a rewrite highlights the whole unmatched area."""
    if end <= start or not glyphs:
        return
    left = min(glyph.x0 for glyph in glyphs)
    right = max(glyph.x1 for glyph in glyphs)
    top = min(glyph.y0 for glyph in glyphs)
    bottom = max(glyph.y1 for glyph in glyphs)
    line_indexes = {glyph.line_index for glyph in glyphs}
    # One drawn line stays on that line, so matched letters inside the span join it.
    # Several lines become one area. A character can sit on only one line.
    line_index = glyphs[0].line_index if len(line_indexes) == 1 else -(10**6 + start)
    for at in range(start, end):
        placed[at] = (line_index, top, bottom, left, right)


def _paint(
    glyph: _Glyph,
    start: int,
    end: int,
    placed: dict[int, tuple[int, float, float, float, float]],
) -> None:
    for at in range(start, end):
        placed[at] = (glyph.line_index, glyph.y0, glyph.y1, glyph.x0, glyph.x1)


def _cover_spans(
    regions: list[_Region],
    text: str,
    start: int,
    end: int,
    placed: dict[int, tuple[int, float, float, float, float]],
) -> None:
    """Each formula span paints its own box onto the characters of its LaTeX."""
    cursor = start
    for region in regions:
        latex = region.latex
        if not latex:
            continue
        found = text.find(latex, cursor, end)
        if found < 0 or found + len(latex) > end:
            continue
        _paint(region.glyph, found, found + len(latex), placed)
        cursor = found + len(latex)


def text_lines_for_block(text: str, raw_lines: object) -> list[TextLine]:
    """Map one block's harvested lines onto ``text``. A line that cannot be placed is omitted."""
    if not isinstance(raw_lines, list) or not text:
        return []
    glyphs, regions = _collect(raw_lines)
    if not glyphs and not regions:
        return []
    by_anchor: dict[int, list[_Region]] = {}
    for region in regions:
        by_anchor.setdefault(region.anchor, []).append(region)
    # autojunk would ignore the letters that show up most often.
    matcher = SequenceMatcher(None, "".join(glyph.char for glyph in glyphs), text, autojunk=False)
    placed: dict[int, tuple[int, float, float, float, float]] = {}
    for tag, glyph_start, glyph_end, text_start, text_end in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(glyph_end - glyph_start):
                glyph = glyphs[glyph_start + offset]
                placed[text_start + offset] = (
                    glyph.line_index,
                    glyph.y0,
                    glyph.y1,
                    glyph.x0,
                    glyph.x1,
                )
        elif tag == "replace":
            _cover_gap(glyphs[glyph_start:glyph_end], text_start, text_end, placed)
        elif tag == "insert":
            anchored = by_anchor.get(glyph_start, [])
            if any(region.latex for region in anchored):
                _cover_spans(anchored, text, text_start, text_end, placed)
            else:
                _cover_gap([region.glyph for region in anchored], text_start, text_end, placed)
    return _lines_from_placed(placed)


def _lines_from_placed(
    placed: dict[int, tuple[int, float, float, float, float]],
) -> list[TextLine]:
    """One line per contiguous run. A formula box between two words stays its own run."""
    if not placed:
        return []
    order = sorted(placed)
    runs: list[list[int]] = []
    for at in order:
        line_index = placed[at][0]
        if runs:
            previous = runs[-1]
            same_line = placed[previous[-1]][0] == line_index
            interrupted = any(
                placed.get(index, (line_index,))[0] != line_index
                for index in range(previous[-1] + 1, at)
            )
            if same_line and not interrupted:
                previous.append(at)
                continue
        runs.append([at])
    lines: list[TextLine] = []
    for run in runs:
        start = run[0]
        end = run[-1] + 1
        x0: list[float | None] = [None] * (end - start)
        x1: list[float | None] = [None] * (end - start)
        _at, top, bottom, _left, _right = placed[run[0]]
        for at in run:
            _, _, _, left, right = placed[at]
            x0[at - start] = left
            x1[at - start] = right
        lines.append(TextLine(start=start, end=end, y0=top, y1=bottom, x0=x0, x1=x1))
    return lines


def line_for_block_box(text: str, bbox: BoundingBox) -> list[TextLine]:
    """One box for a display equation, which has a block box and no character edges."""
    if not text:
        return []
    return [
        TextLine(
            start=0,
            end=len(text),
            y0=bbox.y0,
            y1=bbox.y1,
            x0=[bbox.x0] * len(text),
            x1=[bbox.x1] * len(text),
        )
    ]


def lines_by_block(extension: object) -> dict[tuple[int, int], list[dict[str, Any]]]:
    """``(page_idx, block index)`` to the raw lines harvested from MinerU."""
    if not isinstance(extension, dict) or extension.get("version") != 1:
        return {}
    pages = extension.get("pages")
    if not isinstance(pages, list):
        return {}
    found: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_idx = page.get("page_idx")
        blocks = page.get("blocks")
        if (
            isinstance(page_idx, bool)
            or not isinstance(page_idx, int)
            or not isinstance(blocks, list)
        ):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            index = block.get("index")
            raw_lines = block.get("lines")
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or not isinstance(raw_lines, list)
            ):
                continue
            found[(page_idx, index)] = raw_lines
    return found
