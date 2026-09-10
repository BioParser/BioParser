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


@dataclass(frozen=True, slots=True)
class WordSpan:
    start: int
    end: int
    line: int
    x0: float
    x1: float
    top: float
    bottom: float


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    spans: tuple[WordSpan, ...] = ()


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
        text, spans = _lines_to_text_and_spans(self.lines)
        self.paragraphs.append(
            TextBlock(
                text=text,
                x0=min(line.x0 for line in self.lines),
                x1=max(line.x1 for line in self.lines),
                top=min(line.top for line in self.lines),
                bottom=max(line.bottom for line in self.lines),
                spans=spans,
            )
        )
        self.lines = []


SOFT_HYPHEN = "\u00ad"


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
) -> list[TextBlock]:
    """Join words into lines, then lines into column-aware blocks.

    PDFs do not encode paragraphs. Words with similar `top` become a line,
    split if an x-gap exceeds `column_gutter_factor` times the adjacent
    word height. Several blocks may stay open:
    a line joins the open block whose last line overlaps it in x by more
    than `min_x_overlap`. Other columns are ignored for the gap test. A line that
    overlaps more than one open last-line is treated as spanning and flushes
    those columns. A gap larger than `paragraph_gap_factor` times that
    column's previous line height starts a new paragraph in the same open
    column. Remaining columns are emitted left to right, each top to bottom.
    """
    words = [WordBox(_strip_soft_hyphens(w.text), w.x0, w.x1, w.top, w.bottom) for w in words]
    words = [w for w in words if w.text]
    if not words:
        return []

    lines = _words_to_lines(
        words,
        line_tolerance=line_tolerance,
        column_gutter_factor=column_gutter_factor,
    )
    return _lines_to_blocks(
        lines,
        paragraph_gap_factor=paragraph_gap_factor,
        min_x_overlap=min_x_overlap,
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


def _lines_to_blocks(
    lines: list[_Line],
    *,
    paragraph_gap_factor: float,
    min_x_overlap: float,
) -> list[TextBlock]:
    emitted: list[TextBlock] = []
    open_blocks: list[_OpenBlock] = []
    for line in lines:
        matches = [
            block
            for block in open_blocks
            if _x_ranges_overlap(block.last, line, min_overlap=min_x_overlap)
        ]
        if len(matches) > 1:
            for block in sorted(matches, key=lambda item: item.column_x):
                open_blocks.remove(block)
                block.close_paragraph()
                emitted.extend(block.paragraphs)
            started = _OpenBlock()
            started.add(line)
            open_blocks.append(started)
            continue
        if not matches:
            started = _OpenBlock()
            started.add(line)
            open_blocks.append(started)
            continue
        target = matches[0]
        if _vertical_paragraph_break(target.last, line, factor=paragraph_gap_factor):
            target.close_paragraph()
        target.add(line)
    open_blocks.sort(key=lambda block: block.column_x)
    for block in open_blocks:
        block.close_paragraph()
        emitted.extend(block.paragraphs)
    return emitted


def _lines_to_text_and_spans(lines: list[_Line]) -> tuple[str, tuple[WordSpan, ...]]:
    """Build block text and map each word onto character offsets."""
    text = ""
    spans: list[WordSpan] = []
    for line_index, line in enumerate(lines):
        words = line.words
        if not words:
            continue
        hyphen_join = bool(text.endswith("-") and words[0].text[:1].islower())
        if hyphen_join:
            text = text[:-1]
            if spans:
                last = spans[-1]
                trimmed = WordSpan(
                    last.start,
                    last.end - 1,
                    last.line,
                    last.x0,
                    last.x1,
                    last.top,
                    last.bottom,
                )
                if trimmed.end > trimmed.start:
                    spans[-1] = trimmed
                else:
                    spans.pop()
        elif text:
            text += " "
        for word_index, word in enumerate(words):
            if word_index > 0:
                text += " "
            start = len(text)
            text += word.text
            spans.append(
                WordSpan(
                    start,
                    len(text),
                    line_index,
                    word.x0,
                    word.x1,
                    word.top,
                    word.bottom,
                )
            )
    return text, tuple(spans)
