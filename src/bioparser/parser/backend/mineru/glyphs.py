"""Place MinerU's glyph edges onto a block's stored text.

MinerU's text can insert ``^{}`` and ``_{}`` around a superscript or subscript, and it can
drop a line-end hyphen when the next line continues the word. The edges belong to the
characters MinerU actually measured. That removed hyphen is not given a box.
"""

from __future__ import annotations

from typing import Any

from bioparser.parser.models import TextLine

_MARKERS = ("^{", "_{")
# The same hyphen characters MinerU treats as a line-end break, not an en or em dash.
_LINE_END_HYPHENS = frozenset("-\u00ad\u2010\u2011\u2043")


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


def _skip_markers(text: str, pos: int) -> int:
    while text.startswith(_MARKERS, pos):
        pos += 2
    return pos


def _unit(value: float) -> float:
    return min(1.0, max(0.0, value))


def _dropped_line_break(char: str, previous: str, text: str, pos: int) -> bool:
    """A line-end hyphen MinerU already removed because the next line continues the word."""
    return (
        char in _LINE_END_HYPHENS
        and previous.isalpha()
        and pos < len(text)
        and text[pos].islower()
        and text[pos] != char
    )


def text_lines_for_block(text: str, raw_lines: object) -> list[TextLine]:
    """Map one block's harvested lines onto ``text``. A line that cannot be placed is omitted."""
    if not isinstance(raw_lines, list) or not text:
        return []
    placed: list[tuple[int, float, float, int, float, float]] = []
    pos = 0
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
        previous_glyph = ""
        for char, left, right in _expand(raw.get("glyphs")):
            pos = _skip_markers(text, pos)
            if _dropped_line_break(char, previous_glyph, text, pos):
                previous_glyph = char
                continue
            previous_glyph = char
            if pos < len(text) and text[pos] == char:
                at = pos
                pos += 1
            else:
                found = text.find(char, pos, pos + 8)
                if found < 0:
                    continue
                at = found
                pos = found + 1
            edge_left = _unit(left)
            edge_right = _unit(right)
            if edge_right < edge_left:
                continue
            placed.append((line_index, top, bottom, at, edge_left, edge_right))

    lines: list[TextLine] = []
    by_line: dict[int, list[tuple[float, float, int, float, float]]] = {}
    for line_index, top, bottom, at, left, right in placed:
        by_line.setdefault(line_index, []).append((top, bottom, at, left, right))
    for line_index in sorted(by_line):
        entries = by_line[line_index]
        start = min(at for _, _, at, _, _ in entries)
        end = max(at for _, _, at, _, _ in entries) + 1
        x0: list[float | None] = [None] * (end - start)
        x1: list[float | None] = [None] * (end - start)
        top = entries[0][0]
        bottom = entries[0][1]
        for _, _, at, left, right in entries:
            x0[at - start] = left
            x1[at - start] = right
        lines.append(TextLine(start=start, end=end, y0=top, y1=bottom, x0=x0, x1=x1))
    previous = 0
    fitted: list[TextLine] = []
    for line in lines:
        if line.start < previous:
            continue
        fitted.append(line)
        previous = line.end
    return fitted


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
