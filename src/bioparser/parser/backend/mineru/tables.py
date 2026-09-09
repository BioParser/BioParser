"""Table HTML inside MinerU leaves → ``TableContent``."""

from __future__ import annotations

from html.parser import HTMLParser

from bioparser.parser.backend.mineru.schema import MiddleBlock
from bioparser.parser.backend.mineru.text import bbox_from_points
from bioparser.parser.models import BoundingBox, TableCell, TableContent


class _HTMLTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[TableCell] = []
        self._table_depth = 0
        self._row = -1
        self._col = 0
        self._in_cell = False
        self._text: list[str] = []
        self._rowspan = 1
        self._colspan = 1
        self._occupied: set[tuple[int, int]] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table_depth += 1
            return
        if self._table_depth != 1:
            return
        if tag == "tr":
            self._row += 1
            self._col = 0
            return
        if tag not in {"td", "th"}:
            if tag == "br" and self._in_cell:
                self._text.append(" ")
            return
        while (self._row, self._col) in self._occupied:
            self._col += 1
        attr = {key: value for key, value in attrs if value is not None}
        self._rowspan = _positive_int(attr.get("rowspan"), default=1)
        self._colspan = _positive_int(attr.get("colspan"), default=1)
        self._in_cell = True
        self._text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._table_depth = max(0, self._table_depth - 1)
            return
        if self._table_depth != 1 or tag not in {"td", "th"} or not self._in_cell:
            return
        text = "".join(self._text).strip()
        self.cells.append(
            TableCell(
                row=self._row,
                column=self._col,
                text=text,
                rowspan=self._rowspan,
                colspan=self._colspan,
            )
        )
        for row_off in range(self._rowspan):
            for col_off in range(self._colspan):
                self._occupied.add((self._row + row_off, self._col + col_off))
        self._col += self._colspan
        self._in_cell = False
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._in_cell and self._table_depth == 1:
            self._text.append(data)


def _positive_int(raw: str | None, *, default: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _cells_from_html(html: str) -> list[TableCell]:
    parser = _HTMLTableParser()
    parser.feed(html)
    parser.close()
    return parser.cells


def _table_html_from_leaf(leaf: MiddleBlock) -> str | None:
    for line in leaf.lines:
        for span in line.spans:
            if span.type != "table":
                continue
            html = span.html
            if html is not None and html.strip():
                return html
    return None


def table_from_leaf(
    leaf: MiddleBlock,
    *,
    width: float,
    height: float,
) -> tuple[TableContent, BoundingBox | None] | None:
    html = _table_html_from_leaf(leaf)
    if html is None:
        return None
    cells = _cells_from_html(html)
    if not cells:
        return None
    bbox = bbox_from_points(leaf.bbox, width, height)
    return TableContent(cells=cells), bbox
