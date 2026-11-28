"""Read the glyph geometry the MinerU image attaches to a middle.json.

The image's patch stores ``extensions.bioparser_lines`` (version 1). It is untrusted
input, so this module checks every field and skips what it cannot use. It does no
alignment. ``glyphs.py`` places what this module returns onto the stored text.

The shape, with every coordinate a fraction of the page::

    {"version": 1,
     "pages": [{"page_idx": 0,
                "blocks": [{"index": 3,
                            "lines": [{"y0": 0.1, "y1": 0.2, "glyphs": [GLYPH, ...]}]}]}]}

    GLYPH   {"t": "ab", "x0": 0.1, "x1": 0.2}          text and its horizontal edges
    FORMULA {"t": "", "x0": 0.3, "x1": 0.5,            an inline formula, no characters
             "y0": 0.1, "y1": 0.2, "latex": "x^2"}     y0, y1 and latex are optional

A glyph whose text is longer than one character shares its box in equal slices.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, NamedTuple

# Names the line a character was drawn on. Two characters with the same key sit on the
# same line. A formula or a rewritten span gets a key of its own, so it never joins one.
LineKey = tuple[str, int]


class Glyph(NamedTuple):
    """One character, or the box of one formula, with the line it was drawn on."""

    char: str
    x0: float
    x1: float
    line: LineKey
    y0: float
    y1: float


class Formula(NamedTuple):
    """An inline formula: its box, and the LaTeX it contributes to the stored text.

    ``anchor`` is the number of characters that come before it in reading order.
    """

    anchor: int
    box: Glyph
    latex: str


class BlockGeometry(NamedTuple):
    glyphs: list[Glyph]
    formulas: list[Formula]


def _number(value: object) -> float | None:
    """A real number. A bool is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _clamped(value: object) -> float | None:
    number = _number(value)
    return None if number is None else _clamp(number)


def _dicts(value: object) -> Iterator[dict[str, Any]]:
    """The dict items of a list. Anything else yields nothing."""
    if isinstance(value, list):
        yield from (item for item in value if isinstance(item, dict))


def _characters(item: dict[str, Any]) -> list[tuple[str, float, float]]:
    """One ``(char, x0, x1)`` per character of a glyph, unclamped."""
    text = item.get("t")
    left = _number(item.get("x0"))
    right = _number(item.get("x1"))
    if not isinstance(text, str) or not text or left is None or right is None or right < left:
        return []
    if len(text) == 1:
        return [(text, left, right)]
    step = (right - left) / len(text)
    return [(char, left + step * i, left + step * (i + 1)) for i, char in enumerate(text)]


def _formula(
    item: dict[str, Any], anchor: int, serial: int, top: float, bottom: float
) -> Formula | None:
    """A formula entry. A missing ``y0`` or ``y1`` falls back to its line's span."""
    left = _clamped(item.get("x0"))
    right = _clamped(item.get("x1"))
    if left is None or right is None or right < left:
        return None
    formula_top = _clamped(item.get("y0"))
    formula_bottom = _clamped(item.get("y1"))
    formula_top = top if formula_top is None else formula_top
    formula_bottom = bottom if formula_bottom is None else formula_bottom
    if formula_bottom < formula_top:
        return None
    latex = item.get("latex")
    box = Glyph("", left, right, ("formula", serial), formula_top, formula_bottom)
    return Formula(anchor, box, latex if isinstance(latex, str) else "")


def parse_block(raw_lines: list[Any]) -> BlockGeometry:
    """The characters and formulas of one block, in reading order."""
    glyphs: list[Glyph] = []
    formulas: list[Formula] = []
    for line_index, line in enumerate(_dicts(raw_lines)):
        top = _clamped(line.get("y0"))
        bottom = _clamped(line.get("y1"))
        if top is None or bottom is None or bottom < top:
            continue
        for item in _dicts(line.get("glyphs")):
            if item.get("t") == "":
                formula = _formula(item, len(glyphs), len(formulas) + 1, top, bottom)
                if formula is not None:
                    formulas.append(formula)
                continue
            for char, x0, x1 in _characters(item):
                left, right = _clamp(x0), _clamp(x1)
                if char not in "\r\n" and left <= right:
                    glyphs.append(Glyph(char, left, right, ("line", line_index), top, bottom))
    return BlockGeometry(glyphs, formulas)


def lines_by_block(extension: object) -> dict[tuple[int, int], list[Any]]:
    """``(page_idx, block index)`` to the raw lines harvested from MinerU."""
    if not isinstance(extension, dict) or extension.get("version") != 1:
        return {}
    found: dict[tuple[int, int], list[Any]] = {}
    for page in _dicts(extension.get("pages")):
        page_idx = _int(page.get("page_idx"))
        if page_idx is None:
            continue
        for block in _dicts(page.get("blocks")):
            index = _int(block.get("index"))
            lines = block.get("lines")
            if index is not None and isinstance(lines, list):
                found[(page_idx, index)] = lines
    return found
