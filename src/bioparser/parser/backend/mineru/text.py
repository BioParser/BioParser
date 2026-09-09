"""Text, roles, bboxes, and column/page splits from MinerU leaves."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from bioparser.parser.backend.mineru.schema import BBox, MiddleBlock, MiddleLine
from bioparser.parser.models import BlockRole, BoundingBox, TextContent, TextSpan

TEXT_SPAN_TYPES = frozenset({"text", "inline_equation", "interline_equation"})

CAPTION_TYPES = frozenset(
    {
        "image_caption",
        "table_caption",
        "code_caption",
        "chart_caption",
        "caption",
    }
)

FOOTNOTE_TYPES = frozenset(
    {
        "image_footnote",
        "table_footnote",
        "code_footnote",
        "chart_footnote",
        "page_footnote",
        "footnote",
    }
)

Continuation = Literal["column", "page"]


@dataclass(frozen=True, slots=True)
class TextFragment:
    page_offset: int
    role: BlockRole
    content: TextContent
    bbox: BoundingBox | None


def bbox_from_points(
    raw: BBox,
    width: float,
    height: float,
) -> BoundingBox | None:
    if width <= 0 or height <= 0:
        return None
    x0, y0, x1, y1 = raw
    return BoundingBox(
        x0=_clamp_unit(x0 / width),
        y0=_clamp_unit(y0 / height),
        x1=_clamp_unit(x1 / width),
        y1=_clamp_unit(y1 / height),
    )


def _clamp_unit(value: float) -> float:
    return min(1.0, max(0.0, value))


def role_and_heading(block: MiddleBlock) -> tuple[BlockRole, int | None]:
    block_type = block.type
    if block_type == "title":
        level = block.level if block.level is not None and block.level >= 1 else 1
        return BlockRole.TITLE if level == 1 else BlockRole.HEADING, level
    if block_type in {"list", "index"}:
        return BlockRole.LIST, None
    if block_type in CAPTION_TYPES:
        return BlockRole.CAPTION, None
    if block_type in FOOTNOTE_TYPES:
        return BlockRole.FOOTNOTE, None
    if block_type in {"ref_text", "reference"}:
        return BlockRole.REFERENCE, None
    if block_type in {"header", "footer"}:
        return BlockRole.HEADER if block_type == "header" else BlockRole.FOOTER, None
    return BlockRole.BODY, None


def _append_span_text(existing: str, piece: str) -> str:
    if not existing:
        return piece
    if existing[-1].isspace() or piece[:1].isspace():
        return existing + piece
    return existing + " " + piece


def join_lines_to_text(
    lines: list[MiddleLine],
    width: float,
    height: float,
) -> tuple[str, list[TextSpan], BoundingBox | None]:
    text = ""
    spans: list[TextSpan] = []
    union: tuple[float, float, float, float] | None = None
    for line_index, line in enumerate(lines):
        for raw_span in line.spans:
            if raw_span.type not in TEXT_SPAN_TYPES or not raw_span.content:
                continue
            text = _append_span_text(text, raw_span.content)
            start = len(text) - len(raw_span.content)
            bbox = bbox_from_points(raw_span.bbox, width, height)
            if bbox is None:
                continue
            spans.append(TextSpan(start=start, end=len(text), line=line_index, bbox=bbox))
            union = (
                min(union[0], bbox.x0) if union else bbox.x0,
                min(union[1], bbox.y0) if union else bbox.y0,
                max(union[2], bbox.x1) if union else bbox.x1,
                max(union[3], bbox.y1) if union else bbox.y1,
            )
    block_bbox = BoundingBox(x0=union[0], y0=union[1], x1=union[2], y1=union[3]) if union else None
    return text, spans, block_bbox


def _geometry_kind(
    previous: MiddleLine,
    current: MiddleLine,
    *,
    page_height: float,
) -> Continuation | None:
    prev_box = previous.bbox
    curr_box = current.bbox
    if page_height > 0 and curr_box[1] >= page_height:
        return "page"
    prev_height = max(prev_box[3] - prev_box[1], 1.0)
    jumped_up = curr_box[1] < prev_box[1] - prev_height
    if not jumped_up:
        return None
    gutter = 0.5 * prev_height
    if curr_box[0] > prev_box[2] + gutter:
        return "column"
    return "page"


def _line_cross_page(line: MiddleLine) -> bool:
    return any(span.cross_page for span in line.spans)


def _continuation_kind(
    previous: MiddleLine,
    current: MiddleLine,
    *,
    page_height: float,
) -> Continuation | None:
    """Split a merged MinerU para into one box per column/page fragment.

    MinerU sets ``cross_page`` on every line absorbed from a later page, not
    only the first. Those lines stay in the current fragment unless geometry
    shows a column wrap or another page break. The flag only starts a run.
    """
    geom = _geometry_kind(previous, current, page_height=page_height)
    if geom is not None:
        return geom
    if _line_cross_page(current) and not _line_cross_page(previous):
        return "page"
    return None


def split_lines_by_continuation(
    lines: list[MiddleLine],
    *,
    page_height: float,
) -> list[tuple[int, list[MiddleLine]]]:
    groups: list[tuple[int, list[MiddleLine]]] = []
    current: list[MiddleLine] = []
    page_offset = 0
    previous: MiddleLine | None = None
    for line in lines:
        if previous is not None:
            kind = _continuation_kind(previous, line, page_height=page_height)
            if kind is not None:
                if current:
                    groups.append((page_offset, current))
                    current = []
                if kind == "page":
                    page_offset += 1
        current.append(line)
        previous = line
    if current:
        groups.append((page_offset, current))
    return groups


def fragments_from_leaf(
    leaf: MiddleBlock,
    *,
    page_sizes: list[tuple[float, float]],
    page_index: int,
) -> list[TextFragment]:
    if not leaf.lines:
        return []
    _width, height = page_sizes[page_index]
    role, heading_level = role_and_heading(leaf)
    prepared: list[TextFragment] = []
    for page_offset, group in split_lines_by_continuation(leaf.lines, page_height=height):
        dest = min(page_index + page_offset, len(page_sizes) - 1)
        dest_width, dest_height = page_sizes[dest]
        text, spans, span_union = join_lines_to_text(group, dest_width, dest_height)
        if not text.strip():
            continue
        prepared.append(
            TextFragment(
                page_offset=dest - page_index,
                role=role,
                content=TextContent(text=text, heading_level=heading_level, spans=spans),
                bbox=span_union,
            )
        )
    return prepared
