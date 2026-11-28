"""Runtime half of the MinerU glyph-edge patch.

``apply_patches.py`` installs this file into site-packages and inserts one-line
calls to it in MinerU and DocVortex. Everything with logic lives here, so it is plain
typed Python that the normal tests import. The module uses only the standard library.

The data flow, in the order the pinned packages run:

1. Native PDF text. ``keep_span_glyphs`` copies each character box onto the span
   before MinerU deletes ``chars``. ``char_edges`` does the same on a DocVortex line.
2. OCR. The recognizer returns a column count and one column per character.
   ``column_centers`` turns the columns into per-character centers, and ``ocr_chars``
   places them along the detection box.
3. Tagged PDF text. ``tagged_lines`` groups tagged characters into visual lines.
4. Lines per block. ``line_glyphs`` and ``output_lines`` put glyph edges next to each
   line box. ``start_capture``, ``note_flat``, ``remap_open_page`` and
   ``set_page_idx`` record those lines while a page is still a flat list.
   ``attach_lines`` stores the result as ``extensions.bioparser_lines``.

``extensions.bioparser_lines`` is version 1. ``pages`` is a list of ``{"page_idx", "blocks"}``.
A block is ``{"index", "lines"}`` and a line is ``{"y0", "y1", "glyphs"}``. A glyph is
``{"t", "x0", "x1"}``. A formula span has ``"t": ""``, its own ``y0`` and ``y1``, and
optional ``"latex"``. Coordinates are fractions of the page.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextvars import ContextVar
from itertools import pairwise
from typing import Any

Box = tuple[float, float, float, float]
Glyph = dict[str, object]

# A box in, the same box normalized to the page out. None drops the glyph.
Normalizer = Callable[[Sequence[float]], Sequence[float] | None]


# -- character boxes ---------------------------------------------------------------------


def char_box(char: object) -> tuple[str, Box] | None:
    """The text and raw box of one MinerU character, or None when either is unusable."""
    if not isinstance(char, dict):
        return None
    text = char.get("char")
    if not isinstance(text, str) or text == "":
        return None
    try:
        raw = tuple(float(value) for value in char.get("bbox"))  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return None
    if len(raw) != 4:
        return None
    return text, (raw[0], raw[1], raw[2], raw[3])


def keep_span_glyphs(span: Any) -> None:
    """Copy the character boxes off a MinerU span before ``chars`` is deleted."""
    metadata = span.metadata
    chars = metadata.get("chars") if isinstance(metadata, dict) else None
    if not isinstance(chars, list):
        return
    glyphs: list[Glyph] = []
    for char in chars:
        parsed = char_box(char)
        if parsed is not None:
            glyphs.append({"char": parsed[0], "bbox": list(parsed[1])})
    if glyphs:
        metadata["bioparser_glyphs"] = glyphs


def char_edges(chars: object, to_unit: Normalizer) -> list[Glyph]:
    """Horizontal edges of a DocVortex line's characters. The next step clears ``chars``."""
    glyphs: list[Glyph] = []
    for char in chars or []:  # type: ignore[attr-defined]
        parsed = char_box(char)
        if parsed is None:
            continue
        box = to_unit(parsed[1])
        if box is not None:
            glyphs.append({"t": parsed[0], "x0": box[0], "x1": box[2]})
    return glyphs


def line_glyphs(spans: Sequence[Any], to_unit: Normalizer, equation_type: object) -> list[Glyph]:
    """The glyphs of one MinerU line.

    A span with saved characters contributes their edges. An inline formula with none
    contributes its own box and LaTeX. Any other span contributes nothing.
    """
    glyphs: list[Glyph] = []
    for span in spans:
        saved = span.metadata.get("bioparser_glyphs") if isinstance(span.metadata, dict) else None
        if isinstance(saved, list) and saved:
            for char in saved:
                parsed = char_box(char)
                box = None if parsed is None else to_unit(parsed[1])
                if parsed is not None and box is not None:
                    glyphs.append({"t": parsed[0], "x0": box[0], "x1": box[2]})
            continue
        if span.type != equation_type:
            continue
        box = to_unit(span.bbox)
        if box is None:
            continue
        glyph: Glyph = {"t": "", "x0": box[0], "x1": box[2], "y0": box[1], "y1": box[3]}
        latex = str(span.content or "").strip()
        if latex.startswith("\\(") and latex.endswith("\\)"):
            latex = latex[2:-2].strip()
        if latex:
            glyph["latex"] = latex
        glyphs.append(glyph)
    return glyphs


def output_lines(items: list[dict[str, Any]], members: object) -> list[dict[str, Any]]:
    """DocVortex's public line boxes, each with the glyphs frozen on its source line."""
    if not isinstance(members, list) or len(members) != len(items):
        return items  # No safe pairing, so no line gets another line's glyphs.
    for item, member in zip(items, members, strict=True):
        glyphs = getattr(member, "bioparser_glyphs", None)
        item["glyphs"] = glyphs if isinstance(glyphs, list) else []
    return items


# -- OCR ---------------------------------------------------------------------------------


def sidecar_glyphs(
    raw_chars: object, to_page_box: Callable[[object], Sequence[float] | None]
) -> list[Glyph] | None:
    """Character boxes the OCR step passed along, in page units. One bad box drops them all."""
    if not isinstance(raw_chars, list) or not raw_chars:
        return None
    glyphs: list[Glyph] = []
    for char in raw_chars:
        if not isinstance(char, dict):
            return None
        page_box = to_page_box(char.get("bbox"))
        text = char.get("char")
        if page_box is None or not isinstance(text, str) or text == "":
            return None
        glyphs.append({"char": text, "bbox": list(page_box)})
    return glyphs or None


def column_centers(token_ids: Sequence[Any], kept: Sequence[int]) -> list[float]:
    """Center column of each kept character in a CTC id row.

    ``kept`` indexes the first column of every character. Columns that repeat the same
    id belong to the same character, so the center is the middle of that run.
    """
    column_count = len(token_ids)
    centers: list[float] = []
    for index in kept:
        token = token_ids[index]
        end = int(index) + 1
        while end < column_count and token_ids[end] == token:
            end += 1
        centers.append((int(index) + end - 1) / 2)
    return centers


def ocr_chars(
    text: str,
    info: object,
    bbox: object,
    rotate_ratio: float = 1.5,
) -> list[Glyph] | None:
    """Place each recognized character along a horizontal detection box.

    ``info`` is the column count after padding is removed, then one column center
    per character. That count can stop short of the last center, which used to pile
    the tail on the right edge and drop the line. The last center then sets the
    right edge. A vertical crop is left alone: its columns run down the page.
    """
    if text == "":
        return None
    if not isinstance(info, (list, tuple)) or len(info) != 2:
        return None
    raw_columns = info[1]
    if not isinstance(raw_columns, (list, tuple)):
        return None
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        column_count = float(info[0])
        columns = [float(value) for value in raw_columns]
        x0, y0, x1, y1 = (float(value) for value in bbox)
    except (TypeError, ValueError):
        return None
    width = x1 - x0
    height = y1 - y0
    if len(columns) != len(text) or width <= 0 or height <= 0 or height / width >= rotate_ratio:
        return None
    column_count = max(column_count, max(columns))
    if column_count <= 0:
        return None
    centers = [min(max(column / column_count, 0.0), 1.0) for column in columns]
    # Each character owns the span halfway to its neighbors.
    bounds = [0.0, *((a + b) / 2 for a, b in pairwise(centers)), 1.0]
    chars: list[Glyph] = []
    for char, left, right in zip(text, bounds[:-1], bounds[1:], strict=True):
        if right <= left:
            return None
        chars.append({"char": char, "bbox": [x0 + left * width, y0, x0 + right * width, y1]})
    return chars


# -- tagged PDF text ---------------------------------------------------------------------

# A character joins the current line when its box overlaps the line vertically by this
# share of the smaller height.
_SAME_LINE_OVERLAP = 0.4


def _visible_boxes(chars: object) -> list[tuple[str, Box]]:
    """Visible, single-line, non-empty characters with a real box, in tree order."""
    if not isinstance(chars, list):
        return []
    found: list[tuple[str, Box]] = []
    for char in chars:
        if not isinstance(char, dict) or char.get("text_is_visible") is False:
            continue
        text = char.get("char")
        if not isinstance(text, str):
            continue
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if text == "" or text == "\x00" or "\n" in text:
            continue
        parsed = char_box({"char": text, "bbox": char.get("bbox")})
        if parsed is None:
            continue
        box = parsed[1]
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        found.append((text, box))
    return found


def _group_visual_lines(boxes: list[tuple[str, Box]]) -> list[list[tuple[str, Box]]]:
    """Split characters into runs that share a row. The next run starts its own line."""
    groups: list[list[tuple[str, Box]]] = []
    bounds: list[list[float]] = []
    for text, box in boxes:
        top, bottom = box[1], box[3]
        if bounds:
            line_top, line_bottom = bounds[-1]
            overlap = min(bottom, line_bottom) - max(top, line_top)
            limit = min(bottom - top, line_bottom - line_top)
            if limit > 0 and overlap >= _SAME_LINE_OVERLAP * limit:
                groups[-1].append((text, box))
                bounds[-1] = [min(line_top, top), max(line_bottom, bottom)]
                continue
        groups.append([(text, box)])
        bounds.append([top, bottom])
    return groups


def tagged_lines(chars: object, page_size: object) -> list[dict[str, object]]:
    """One normalized line per visual run, with each character's own horizontal edges.

    Characters stay in the order the structure tree produced. A paragraph is not one
    tall box, because the next run starts its own line.
    """
    try:
        width = float(page_size[0])  # type: ignore[index]
        height = float(page_size[1])  # type: ignore[index]
    except (TypeError, ValueError, IndexError):
        return []
    if width <= 0 or height <= 0:
        return []

    def unit(value: float, size: float) -> float:
        return min(max(value / size, 0.0), 1.0)

    lines: list[dict[str, object]] = []
    for group in _group_visual_lines(_visible_boxes(chars)):
        glyphs: list[Glyph] = []
        boxes: list[Box] = []
        for text, raw in group:
            box = (
                unit(raw[0], width),
                unit(raw[1], height),
                unit(raw[2], width),
                unit(raw[3], height),
            )
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            glyphs.append({"t": text, "x0": box[0], "x1": box[2]})
            boxes.append(box)
        if glyphs:
            lines.append(
                {
                    "bbox": [
                        min(box[0] for box in boxes),
                        min(box[1] for box in boxes),
                        max(box[2] for box in boxes),
                        max(box[3] for box in boxes),
                    ],
                    "glyphs": glyphs,
                }
            )
    return lines


# -- capture while a page is a flat list -------------------------------------------------

_captured: ContextVar[dict[str, Any] | None] = ContextVar("bioparser_lines", default=None)


def start_capture() -> None:
    """Begin a document. Called when DocVortex starts to build its pages."""
    _captured.set({"version": 1, "pages": []})


def _glyph_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, object]]:
    """Blocks of a flat page that have glyph lines, keyed by the index each one just got."""
    found: list[dict[str, object]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        index = block.get("index")
        # Tagged text keeps its measured lines apart from the public one-box line.
        lines = block.get("_lines") or block.get("lines")
        if not isinstance(index, int) or isinstance(index, bool):
            continue
        if not isinstance(lines, list) or not lines:
            continue
        kept: list[dict[str, object]] = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            bbox = line.get("bbox")
            glyphs = line.get("glyphs")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            if not isinstance(glyphs, list):
                continue
            kept.append({"y0": bbox[1], "y1": bbox[3], "glyphs": glyphs})
        if kept:
            found.append({"index": index, "lines": kept})
    return found


def _open_page() -> dict[str, Any] | None:
    """The page that has blocks but no ``page_idx`` yet, if any."""
    captured = _captured.get()
    if not isinstance(captured, dict):
        return None
    pages = captured.setdefault("pages", [])
    if pages and pages[-1].get("page_idx") is None:
        return pages[-1]  # type: ignore[no-any-return]
    return None


def note_flat(blocks: list[dict[str, Any]]) -> None:
    """Record glyph lines while the page is still a flat list."""
    found = _glyph_blocks(blocks)
    captured = _captured.get()
    if not found or not isinstance(captured, dict):
        return
    open_page = _open_page()
    if open_page is None:
        captured.setdefault("pages", []).append({"page_idx": None, "blocks": found})
        return
    on_page = open_page.setdefault("blocks", [])
    if isinstance(on_page, list):
        on_page.extend(found)


def remap_open_page(positions: list[int]) -> None:
    """Tagged pages renumber the flat list after the snapshot. Follow that renumber."""
    open_page = _open_page()
    if open_page is None:
        return
    for block in open_page.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        index = block.get("index")
        if type(index) is int and 0 <= index < len(positions):
            block["index"] = positions[index]


def set_page_idx(page_idx: int) -> None:
    """Name the open page. DocVortex knows the page number only after the blocks."""
    open_page = _open_page()
    if open_page is not None:
        open_page["page_idx"] = page_idx


def attach_lines(middle_json: Any) -> Any:
    """Store the captured lines on the finished document and return it."""
    captured = _captured.get()
    _captured.set(None)  # Nothing from this document may reach the next one.
    if isinstance(captured, dict) and captured.get("pages"):
        middle_json.extensions = {**middle_json.extensions, "bioparser_lines": captured}
    return middle_json
