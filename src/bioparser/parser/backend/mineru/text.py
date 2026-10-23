"""Text, roles and bboxes from MinerU leaves."""

from __future__ import annotations

from bioparser.parser.backend.mineru.schema import BBox, MiddleBlock, MiddleSpan
from bioparser.parser.models import BlockRole, BoundingBox

CAPTION_TYPES = frozenset({"image_caption", "table_caption", "chart_caption", "code_caption"})

FOOTNOTE_TYPES = frozenset(
    {
        "image_footnote",
        "table_footnote",
        "chart_footnote",
        "code_footnote",
        "page_footnote",
    }
)


def bbox_from_unit(raw: BBox | None) -> BoundingBox | None:
    if raw is None:
        return None
    x0, y0, x1, y1 = (min(1.0, max(0.0, value)) for value in raw)
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)


def role_and_heading(block: MiddleBlock, *, in_list: bool) -> tuple[BlockRole, int | None]:
    """Role of a leaf. Items of a list or index are list entries, references stay references."""
    match block.type:
        case "ref_text":
            return BlockRole.REFERENCE, None
        case _ if in_list:
            return BlockRole.LIST, None
        case "doc_title":
            return BlockRole.TITLE, 1
        case "paragraph_title":
            return BlockRole.HEADING, max(block.level or 2, 2)
        case kind if kind in CAPTION_TYPES:
            return BlockRole.CAPTION, None
        case kind if kind in FOOTNOTE_TYPES:
            return BlockRole.FOOTNOTE, None
        case _:
            return BlockRole.BODY, None


def _span_text(span: MiddleSpan) -> str:
    if not isinstance(span.content, str):
        return _spans_text(span.content)
    # In LaTeX, like inline equations, so 10 with a superscript 9 stays 10^{9}, not 109.
    if span.content and "superscript" in span.styles:
        return f"^{{{span.content}}}"
    if span.content and "subscript" in span.styles:
        return f"_{{{span.content}}}"
    return span.content


def _spans_text(spans: list[MiddleSpan]) -> str:
    # Span contents back to back, a link by its children.
    return "".join(_span_text(span) for span in spans)


def text_from_leaf(leaf: MiddleBlock) -> str:
    return leaf.content if isinstance(leaf.content, str) else _spans_text(leaf.content)
