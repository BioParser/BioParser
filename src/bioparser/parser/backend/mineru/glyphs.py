"""Place MinerU's glyph edges onto a block's stored text.

MinerU measured a box for each character it drew. The stored text is not always what
was drawn: a formula became LaTeX, a hyphen was removed, a ligature was split. So the
two strings are aligned as a diff, and each stored character takes a box from the diff:

* Characters that still agree keep the box that was measured for them.
* A rewritten span has no box of its own. Every one of its characters shares one box,
  the union of the ink that did not match, whatever lines that ink was drawn on.
* An inline formula often has no characters left, only the box MinerU measured for it.
  That box goes to the characters of its LaTeX.
* Inserted text with no glyphs and no box stays empty.
* A glyph with no stored character is dropped.

The result is a ``TextLine`` per run of characters on one drawn line. ``glyph_wire.py``
reads the measured geometry. This module only places it.

Spaces are left out of the diff. A missing space must not drop the words after it, and
a space must not be a character the matcher can slide on. A space that both sides still
have keeps its own box afterwards.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from itertools import pairwise
from typing import NamedTuple

from bioparser.parser.backend.mineru.glyph_wire import (
    BlockGeometry,
    Formula,
    Glyph,
    LineKey,
    parse_block,
)
from bioparser.parser.models import BoundingBox, TextLine


class _Placed(NamedTuple):
    """The box one stored character takes, and the drawn line it belongs to."""

    line: LineKey
    y0: float
    y1: float
    x0: float
    x1: float

    @classmethod
    def of(cls, glyph: Glyph) -> _Placed:
        return cls(glyph.line, glyph.y0, glyph.y1, glyph.x0, glyph.x1)


# Index into the stored text to the box that character takes. A missing key is a
# character with no ink.
_Placements = dict[int, _Placed]


def _spread(glyphs: list[Glyph], start: int, end: int, placed: _Placements) -> None:
    """Give every character in ``text[start:end]`` the union box of ``glyphs``."""
    if end <= start or not glyphs:
        return
    lines = {glyph.line for glyph in glyphs}
    box = _Placed(
        # Ink on one drawn line stays on that line, so matched letters inside the span
        # join it. Ink on several lines becomes one area with a line of its own.
        line=glyphs[0].line if len(lines) == 1 else ("area", start),
        y0=min(glyph.y0 for glyph in glyphs),
        y1=max(glyph.y1 for glyph in glyphs),
        x0=min(glyph.x0 for glyph in glyphs),
        x1=max(glyph.x1 for glyph in glyphs),
    )
    for at in range(start, end):
        placed[at] = box


def _paint_latex(
    formulas: list[Formula], text: str, start: int, end: int, placed: _Placements
) -> None:
    """Find each formula's LaTeX in ``text[start:end]`` and give it that formula's box."""
    cursor = start
    for formula in formulas:
        if not formula.latex:
            continue
        found = text.find(formula.latex, cursor, end)
        if found < 0 or found + len(formula.latex) > end:
            continue
        cursor = found + len(formula.latex)
        box = _Placed.of(formula.box)
        for at in range(found, cursor):
            placed[at] = box


class _Placer:
    """Aligns one block's glyphs with its stored text and fills in ``placed``.

    The diff runs on the glyph and text strings with spaces removed. ``glyph_at`` and
    ``text_at`` map a position in those compact strings back to an index in ``glyphs``
    and ``text``. Every method below takes compact positions and converts as it goes.
    """

    def __init__(self, text: str, geometry: BlockGeometry) -> None:
        self.text = text
        self.glyphs = geometry.glyphs
        self.formulas_at: dict[int, list[Formula]] = {}
        for formula in geometry.formulas:
            self.formulas_at.setdefault(formula.anchor, []).append(formula)
        self.glyph_at = [i for i, glyph in enumerate(self.glyphs) if glyph.char != " "]
        self.text_at = [i for i, char in enumerate(text) if char != " "]
        self.placed: _Placements = {}

    def run(self) -> _Placements:
        matcher = SequenceMatcher(
            None,
            "".join(self.glyphs[i].char for i in self.glyph_at),
            "".join(self.text[i] for i in self.text_at),
            autojunk=False,  # it would ignore the letters that show up most often
        )
        for tag, glyph_start, glyph_end, text_start, text_end in matcher.get_opcodes():
            if tag == "equal":
                self._matched(glyph_start, glyph_end, text_start)
            elif tag == "replace":
                self._rewritten(glyph_start, glyph_end, text_start, text_end)
            elif tag == "insert":
                self._inserted(glyph_start, text_start, text_end)
            # "delete" is ink with no stored character. It is dropped.
        return self.placed

    def _matched(self, glyph_start: int, glyph_end: int, text_start: int) -> None:
        """Letters both sides agree on keep their box, and so do the spaces between them."""
        pairs = [
            (self.glyph_at[glyph_start + k], self.text_at[text_start + k])
            for k in range(glyph_end - glyph_start)
        ]
        for glyph_pos, text_pos in pairs:
            self.placed[text_pos] = _Placed.of(self.glyphs[glyph_pos])
        for (glyph_before, text_before), (glyph_after, text_after) in pairwise(pairs):
            glyph_spaces = [
                self.glyphs[i]
                for i in range(glyph_before + 1, glyph_after)
                if self.glyphs[i].char == " "
            ]
            text_spaces = [i for i in range(text_before + 1, text_after) if self.text[i] == " "]
            # Spaces pair up in order. An extra space on either side stays empty.
            for text_pos, glyph in zip(text_spaces, glyph_spaces, strict=False):
                self.placed[text_pos] = _Placed.of(glyph)

    def _rewritten(self, glyph_start: int, glyph_end: int, text_start: int, text_end: int) -> None:
        """Stored text replaced the drawn ink. All of it shares the ink's union box."""
        _spread(
            [self.glyphs[self.glyph_at[i]] for i in range(glyph_start, glyph_end)],
            self.text_at[text_start],
            self.text_at[text_end - 1] + 1,
            self.placed,
        )

    def _inserted(self, glyph_start: int, text_start: int, text_end: int) -> None:
        """Stored text with no ink. A formula that sits at this spot may account for it."""
        start = self.text_at[text_start]
        end = self.text_at[text_end - 1] + 1
        formulas = self._formulas_before(glyph_start)
        if any(formula.latex for formula in formulas):
            _paint_latex(formulas, self.text, start, end, self.placed)
        else:
            _spread([formula.box for formula in formulas], start, end, self.placed)

    def _formulas_before(self, glyph_pos: int) -> list[Formula]:
        """Formulas that sit after the previous letter and before this one.

        That includes formulas anchored among the spaces between the two.
        """
        first = self.glyph_at[glyph_pos - 1] + 1 if glyph_pos else 0
        last = self.glyph_at[glyph_pos] if glyph_pos < len(self.glyph_at) else len(self.glyphs)
        return [
            formula
            for anchor in range(first, last + 1)
            for formula in self.formulas_at.get(anchor, [])
        ]


def _runs(placed: _Placements) -> list[list[int]]:
    """Group placed characters into runs: neighbors on the same drawn line.

    A character with a different line between two others breaks the run, so a formula
    box between two words stays its own run.
    """
    runs: list[list[int]] = []
    for at in sorted(placed):
        if runs:
            previous = runs[-1][-1]
            line = placed[at].line
            interrupted = any(
                index in placed and placed[index].line != line for index in range(previous + 1, at)
            )
            if placed[previous].line == line and not interrupted:
                runs[-1].append(at)
                continue
        runs.append([at])
    return runs


def _line_for_run(run: list[int], placed: _Placements) -> TextLine:
    start, end = run[0], run[-1] + 1
    x0: list[float | None] = [None] * (end - start)
    x1: list[float | None] = [None] * (end - start)
    for at in run:
        x0[at - start] = placed[at].x0
        x1[at - start] = placed[at].x1
    first = placed[start]
    return TextLine(start=start, end=end, y0=first.y0, y1=first.y1, x0=x0, x1=x1)


def text_lines_for_block(text: str, raw_lines: object) -> list[TextLine]:
    """Map one block's harvested lines onto ``text``. A line that cannot be placed is omitted."""
    if not isinstance(raw_lines, list) or not text:
        return []
    geometry = parse_block(raw_lines)
    if not geometry.glyphs and not geometry.formulas:
        return []
    placed = _Placer(text, geometry).run()
    return [_line_for_run(run, placed) for run in _runs(placed)]


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
