from __future__ import annotations

from dataclasses import dataclass, field
from typing import Self


@dataclass(frozen=True, slots=True)
class WordBox:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    # One glyph box. Its text may be longer than one character. Empty means the word box is split evenly.
    glyphs: tuple[tuple[str, float, float], ...] = ()


@dataclass(frozen=True, slots=True)
class LineGeom:
    """Character edges of one visual line, in the block string's coordinates (PDF points)."""

    start: int
    end: int
    top: float
    bottom: float
    x0: tuple[float | None, ...]
    x1: tuple[float | None, ...]


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    lines: tuple[LineGeom, ...] = ()


@dataclass(frozen=True, slots=True)
class _Line:
    words: tuple[WordBox, ...]
    x0: float
    x1: float
    top: float
    bottom: float

    @classmethod
    def of(cls, words: tuple[WordBox, ...]) -> Self:
        return cls(
            words=words,
            x0=min(w.x0 for w in words),
            x1=max(w.x1 for w in words),
            top=min(w.top for w in words),
            bottom=max(w.bottom for w in words),
        )

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 1.0)


@dataclass(slots=True)
class _OpenBlock:
    lines: list[_Line] = field(default_factory=list)
    paragraphs: list[TextBlock] = field(default_factory=list)
    column_x: float = 0.0

    def add(self, line: _Line) -> None:
        if not self.lines and not self.paragraphs:
            self.column_x = line.x0
        self.lines.append(line)

    @property
    def last(self) -> _Line:
        return self.lines[-1]

    def close_paragraph(self) -> None:
        if not self.lines:
            return
        text, lines = _lines_to_text_and_lines(self.lines)
        self.paragraphs.append(
            TextBlock(
                text=text,
                x0=min(line.x0 for line in self.lines),
                x1=max(line.x1 for line in self.lines),
                top=min(line.top for line in self.lines),
                bottom=max(line.bottom for line in self.lines),
                lines=lines,
            )
        )
        self.lines = []


SOFT_HYPHEN = "\u00ad"
SPAN_WIDTH_FACTOR = 0.7  # line width / page width; at or above this, close columns (band break)


def _strip_soft_hyphens(text: str) -> str:
    """Remove PDF optional-break marks (U+00AD), not visible hyphenation."""
    return text.replace(SOFT_HYPHEN, "").strip()


def group_words_into_text_blocks(
    words: list[WordBox],
    *,
    line_tolerance: float,
    paragraph_gap_factor: float,
    column_gutter_factor: float = 1.0,
    min_x_overlap: float = 8.0,
    page_width: float | None = None,
) -> list[TextBlock]:
    """Join words into lines, then lines into column-aware blocks.

    PDFs do not encode paragraphs. Words with similar `top` become a line,
    split if an x-gap exceeds `column_gutter_factor` times the adjacent
    word height. Several blocks may stay open:
    a line joins the open block whose last line overlaps it in x by more
    than `min_x_overlap`. Other columns are ignored for the gap test. A line
    covering at least `SPAN_WIDTH_FACTOR` of the page width closes every
    open column (left to right) before it, then starts a new band. A gap
    larger than `paragraph_gap_factor` times that column's previous line
    height starts a new paragraph in the same open column. Remaining columns
    are emitted left to right, each top to bottom.
    """
    words = [
        WordBox(
            _strip_soft_hyphens(word.text),
            word.x0,
            word.x1,
            word.top,
            word.bottom,
            glyphs=tuple(
                (_strip_soft_hyphens(text), left, right)
                for text, left, right in word.glyphs
                if _strip_soft_hyphens(text)
            ),
        )
        for word in words
    ]
    words = [w for w in words if w.text]
    if not words:
        return []
    if page_width is None:
        page_width = max(word.x1 for word in words) - min(word.x0 for word in words)

    lines = _words_to_lines(
        words,
        line_tolerance=line_tolerance,
        column_gutter_factor=column_gutter_factor,
    )
    return _lines_to_blocks(
        lines,
        paragraph_gap_factor=paragraph_gap_factor,
        min_x_overlap=min_x_overlap,
        page_width=page_width,
    )


def _x_overlap_width(left: _Line, right: _Line) -> float:
    return max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))


def _x_ranges_overlap(left: _Line, right: _Line, *, min_overlap: float) -> bool:
    return _x_overlap_width(left, right) > min_overlap


def _word_height(word: WordBox) -> float:
    return max(word.bottom - word.top, 1.0)


def _split_line_by_gutter(band: list[WordBox], *, column_gutter_factor: float) -> list[_Line]:
    ordered = sorted(band, key=lambda word: word.x0)
    segments: list[list[WordBox]] = [[ordered[0]]]
    for word in ordered[1:]:
        previous = segments[-1][-1]
        gap = word.x0 - previous.x1
        size = max(_word_height(previous), _word_height(word))
        if gap > column_gutter_factor * size:
            segments.append([word])
        else:
            segments[-1].append(word)
    return [_Line.of(tuple(segment)) for segment in segments]


def _words_to_lines(
    words: list[WordBox],
    *,
    line_tolerance: float,
    column_gutter_factor: float,
) -> list[_Line]:
    ordered = sorted(words, key=lambda w: (w.top, w.x0))
    lines: list[_Line] = []
    current: list[WordBox] = []
    current_top: float | None = None
    for word in ordered:
        if current_top is None or abs(word.top - current_top) <= line_tolerance:
            current.append(word)
            if current_top is None:
                current_top = word.top
            continue
        lines.extend(_split_line_by_gutter(current, column_gutter_factor=column_gutter_factor))
        current = [word]
        current_top = word.top
    if current:
        lines.extend(_split_line_by_gutter(current, column_gutter_factor=column_gutter_factor))
    return lines


def _vertical_paragraph_break(previous: _Line, current: _Line, *, factor: float) -> bool:
    gap = current.top - previous.bottom
    return gap > factor * previous.height


def _take_columns(open_blocks: list[_OpenBlock]) -> list[TextBlock]:
    open_blocks.sort(key=lambda block: block.column_x)
    taken: list[TextBlock] = []
    for column in open_blocks:
        column.close_paragraph()
        taken.extend(column.paragraphs)
    open_blocks.clear()
    return taken


def _lines_to_blocks(
    lines: list[_Line],
    *,
    paragraph_gap_factor: float,
    min_x_overlap: float,
    page_width: float,
) -> list[TextBlock]:
    emitted: list[TextBlock] = []
    open_blocks: list[_OpenBlock] = []
    span_width = SPAN_WIDTH_FACTOR * page_width
    for line in lines:
        if page_width > 0 and (line.x1 - line.x0) >= span_width:
            matches = [
                block
                for block in open_blocks
                if _x_ranges_overlap(block.last, line, min_overlap=min_x_overlap)
            ]
            if len(open_blocks) == 1 and matches:
                target = matches[0]
                if _vertical_paragraph_break(target.last, line, factor=paragraph_gap_factor):
                    target.close_paragraph()
                target.add(line)
                continue
            emitted.extend(_take_columns(open_blocks))
            started = _OpenBlock()
            started.add(line)
            open_blocks.append(started)
            continue
        matches = [
            block
            for block in open_blocks
            if _x_ranges_overlap(block.last, line, min_overlap=min_x_overlap)
        ]
        if not matches:
            started = _OpenBlock()
            started.add(line)
            open_blocks.append(started)
            continue
        target = matches[0]
        if _vertical_paragraph_break(target.last, line, factor=paragraph_gap_factor):
            target.close_paragraph()
        target.add(line)
    emitted.extend(_take_columns(open_blocks))
    return emitted


def _glyph_edges(word: WordBox) -> list[tuple[str, float, float]]:
    """Character edges for ``word.text``. Real glyph boxes win; otherwise the word box is split evenly."""
    if word.glyphs and "".join(text for text, _, _ in word.glyphs) == word.text:
        edges: list[tuple[str, float, float]] = []
        for text, x0, x1 in word.glyphs:
            if len(text) == 1:
                edges.append((text, x0, x1))
                continue
            span = x1 - x0
            step = span / len(text) if text else 0.0
            edges.extend(
                (char, x0 + step * index, x0 + step * (index + 1))
                for index, char in enumerate(text)
            )
        return edges
    count = len(word.text)
    if count == 0:
        return []
    step = (word.x1 - word.x0) / count
    return [
        (char, word.x0 + step * index, word.x0 + step * (index + 1))
        for index, char in enumerate(word.text)
    ]


def _lines_to_text_and_lines(lines: list[_Line]) -> tuple[str, tuple[LineGeom, ...]]:
    """Build block text and one edge list per visual line."""
    text = ""
    geoms: list[LineGeom] = []
    for line in lines:
        words = line.words
        if not words:
            continue
        hyphen_join = bool(text.endswith("-") and words[0].text[:1].islower())
        if hyphen_join:
            text = text[:-1]
            if geoms:
                last = geoms[-1]
                if last.end - 1 > last.start:
                    geoms[-1] = LineGeom(
                        last.start,
                        last.end - 1,
                        last.top,
                        last.bottom,
                        last.x0[:-1],
                        last.x1[:-1],
                    )
                else:
                    geoms.pop()
        elif text:
            text += " "
        x0: list[float | None] = []
        x1: list[float | None] = []
        for word_index, word in enumerate(words):
            if word_index > 0:
                text += " "
                x0.append(None)
                x1.append(None)
            for _char, left, right in _glyph_edges(word):
                text += _char
                x0.append(left)
                x1.append(right)
        geoms.append(
            LineGeom(len(text) - len(x0), len(text), line.top, line.bottom, tuple(x0), tuple(x1))
        )
    return text, tuple(geoms)
