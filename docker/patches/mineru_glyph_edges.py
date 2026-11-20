"""Copy MinerU 4.2.3 character boxes out before they are deleted.

MinerU puts each native character on ``span.metadata["chars"]``, turns that list into the
span text, and then deletes it. Basic and above can rebuild that text in Rust and skip
the character list. The line builder runs after the delete, so a line box is
all that remains. DocVortex 0.6.24 then deletes ``lines`` while it builds the public
document. This rewrites those spots so the middle document gains
``extensions.bioparser_lines``. A span with no character boxes keeps that span's box as one
glyph. BioParser then shares the box evenly across the span text. MinerU can rebuild the
text without character boxes, and the assigner still leaves some spans empty. It
refuses to edit a file that does not still contain the functions it was written against.
"""

from __future__ import annotations

import sys
from pathlib import Path

_KEEP_OLD = """def chars_to_content(
"""

_KEEP_NEW = '''def _bioparser_keep_glyphs(span: _AnalyzeSpan) -> None:
    """Copy character boxes off the span before ``chars`` is deleted."""
    chars = span.metadata.get("chars") if isinstance(span.metadata, dict) else None
    glyphs: list[dict[str, object]] = []
    if isinstance(chars, list):
        for char in chars:
            if not isinstance(char, dict):
                continue
            text = char.get("char")
            if not isinstance(text, str) or text == "":
                continue
            try:
                raw = tuple(float(value) for value in char.get("bbox"))
            except (TypeError, ValueError):
                continue
            if len(raw) != 4:
                continue
            glyphs.append({"char": text, "bbox": list(raw)})
    span.metadata["bioparser_glyphs"] = glyphs


def chars_to_content(
'''

_DELETE_CONTENT_OLD = """        if any(role in {"sup", "sub"} and text for role, text in role_text_parts):
            span.metadata[PDF_NATIVE_SCRIPT_MARKUP_KEY] = True

    del span.metadata["chars"]
"""

_DELETE_CONTENT_NEW = """        if any(role in {"sup", "sub"} and text for role, text in role_text_parts):
            span.metadata[PDF_NATIVE_SCRIPT_MARKUP_KEY] = True

    _bioparser_keep_glyphs(span)
    del span.metadata["chars"]
"""

_DELETE_PREBUILT_OLD = """            if text is not None:
                span.content = text
            del span.metadata["chars"]
"""

_DELETE_PREBUILT_NEW = """            if text is not None:
                span.content = text
            _bioparser_keep_glyphs(span)
            del span.metadata["chars"]
"""

_ASSIGN_OLD = """    assigned_span_indices = _owned_span_assignments(_native_text, spans, median_span_height) if native_contents is None else []
"""

_ASSIGN_NEW = """    assigned_span_indices = _owned_span_assignments(_native_text, spans, median_span_height)
"""

_LINE_ITEMS_OLD = '''def _build_ocr_det_line_items(lines: list[_AnalyzeLine], page_size: tuple[float, float]) -> list[dict[str, Any]]:
    """将 Analyze 私有行转换为归一化行框。"""
    line_items = []
    for line in lines:
        normalized_bbox = _page_bbox_to_unit_bbox(line.bbox, page_size)
        if normalized_bbox is not None:
            line_items.append({"bbox": normalized_bbox})
    return line_items
'''

_LINE_ITEMS_NEW = '''def _bioparser_glyph(char: object, page_size: tuple[float, float]) -> dict[str, object] | None:
    """One measured character, normalized to the page."""
    if not isinstance(char, dict):
        return None
    text = char.get("char")
    if not isinstance(text, str) or text == "":
        return None
    try:
        raw_bbox = tuple(float(value) for value in char.get("bbox"))
    except (TypeError, ValueError):
        return None
    if len(raw_bbox) != 4:
        return None
    normalized = _page_bbox_to_unit_bbox(raw_bbox, page_size)
    if normalized is None:
        return None
    return {"t": text, "x0": normalized[0], "x1": normalized[2]}


def _build_ocr_det_line_items(lines: list[_AnalyzeLine], page_size: tuple[float, float]) -> list[dict[str, Any]]:
    """将 Analyze 私有行转换为归一化行框，并保留每个字形的水平边缘。"""
    line_items = []
    for line in lines:
        normalized_bbox = _page_bbox_to_unit_bbox(line.bbox, page_size)
        if normalized_bbox is None:
            continue
        glyphs: list[dict[str, object]] = []
        for span in line.spans:
            saved = span.metadata.get("bioparser_glyphs") if isinstance(span.metadata, dict) else None
            if not isinstance(saved, list):
                continue
            if saved:
                for char in saved:
                    glyph = _bioparser_glyph(char, page_size)
                    if glyph is not None:
                        glyphs.append(glyph)
                continue
            text = span.content if isinstance(getattr(span, "content", None), str) else ""
            if text == "":
                continue
            try:
                box = _page_bbox_to_unit_bbox(span.bbox, page_size)
            except (TypeError, ValueError):
                box = None
            if box is None:
                continue
            glyphs.append({"t": text, "x0": box[0], "x1": box[2]})
        line_items.append({"bbox": normalized_bbox, "glyphs": glyphs})
    return line_items
'''

_PAGES_IMPORT_OLD = """from copy import deepcopy
from typing import Any
"""

_PAGES_IMPORT_NEW = """from contextvars import ContextVar
from copy import deepcopy
from typing import Any
"""

_PAGES_CAPTURE = '''
bioparser_lines_var: ContextVar[dict[str, object] | None] = ContextVar("bioparser_lines", default=None)


def _bioparser_kept(blocks: list[dict[str, Any]]) -> list[dict[str, object]]:
    """Glyph lines from a flat page, keyed by the index just assigned to each block."""
    kept_blocks: list[dict[str, object]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        index = block.get("index")
        lines = block.get("lines")
        if not isinstance(index, int) or isinstance(index, bool) or not isinstance(lines, list) or not lines:
            continue
        kept: list[dict[str, object]] = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            bbox = line.get("bbox")
            glyphs = line.get("glyphs")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or not isinstance(glyphs, list):
                continue
            kept.append({"y0": bbox[1], "y1": bbox[3], "glyphs": glyphs})
        if kept:
            kept_blocks.append({"index": index, "lines": kept})
    return kept_blocks


def _bioparser_open_page() -> dict[str, object] | None:
    harvested = bioparser_lines_var.get()
    if not isinstance(harvested, dict):
        return None
    pages = harvested.setdefault("pages", [])
    if pages and pages[-1].get("page_idx") is None:
        return pages[-1]
    return None


def _bioparser_note_flat(blocks: list[dict[str, Any]]) -> None:
    """Record glyph lines while the page is still a flat list."""
    kept = _bioparser_kept(blocks)
    if not kept:
        return
    harvested = bioparser_lines_var.get()
    if not isinstance(harvested, dict):
        return
    pages = harvested.setdefault("pages", [])
    open_page = _bioparser_open_page()
    if open_page is None:
        pages.append({"page_idx": None, "blocks": kept})
        return
    blocks_on_page = open_page.setdefault("blocks", [])
    if isinstance(blocks_on_page, list):
        blocks_on_page.extend(kept)


def _bioparser_remap_open_page(positions: list[int]) -> None:
    """Tagged pages renumber the flat list after the snapshot. Follow that renumber."""
    open_page = _bioparser_open_page()
    if open_page is None:
        return
    for block in open_page.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        index = block.get("index")
        if type(index) is int and 0 <= index < len(positions):
            block["index"] = positions[index]


def _bioparser_set_page_idx(page_idx: int) -> None:
    open_page = _bioparser_open_page()
    if open_page is not None:
        open_page["page_idx"] = page_idx


'''

_PAGES_START_OLD = '''def model_json_to_pages(model_json: ModelJson, *, structure_extension: dict | None = None) -> list[PageInfo]:
    """从严格 ModelJson 无副作用地构造可递归序列化的 PageInfo。"""
    page_indices = model_json.resolved_page_indices
'''

_PAGES_START_NEW = '''def model_json_to_pages(model_json: ModelJson, *, structure_extension: dict | None = None) -> list[PageInfo]:
    """从严格 ModelJson 无副作用地构造可递归序列化的 PageInfo。"""
    bioparser_lines_var.set({"version": 1, "pages": []})
    page_indices = model_json.resolved_page_indices
'''

_PAGES_STAMP_OLD = """    page_blocks = process_page_blocks(page_model_list, use_bbox=use_bbox)
    page_blocks.sort(key=lambda block: block["index"])
"""

_PAGES_STAMP_NEW = """    page_blocks = process_page_blocks(page_model_list, use_bbox=use_bbox)
    _bioparser_set_page_idx(page_idx)
    page_blocks.sort(key=lambda block: block["index"])
"""

_PAGE_BLOCKS_NOTE_OLD = """    blocks = _normalize_raw_blocks(page_model_list)
    _apply_caption_fallbacks(blocks, use_bbox=resolved_use_bbox)
"""

_PAGE_BLOCKS_NOTE_NEW = """    blocks = _normalize_raw_blocks(page_model_list)
    from .pages import _bioparser_note_flat

    _bioparser_note_flat(blocks)
    _apply_caption_fallbacks(blocks, use_bbox=resolved_use_bbox)
"""

_STRUCTURE_REMAP_OLD = """    for block in output:
        remap(block)
    trusted = [finalize_structure_block(block, index) for index, block in enumerate(page_model_list) if STRUCTURE_KEY in block]
"""

_STRUCTURE_REMAP_NEW = """    for block in output:
        remap(block)
    from .pages import _bioparser_note_flat, _bioparser_remap_open_page

    _bioparser_remap_open_page(positions)
    trusted = [finalize_structure_block(block, index) for index, block in enumerate(page_model_list) if STRUCTURE_KEY in block]
"""

_STRUCTURE_NOTE_OLD = """            output.append(block)
    return output
"""

_STRUCTURE_NOTE_NEW = """            output.append(block)
    _bioparser_note_flat(trusted)
    return output
"""

_DOC_IMPORT_OLD = "from .pages import model_json_to_pages\n"

_DOC_IMPORT_NEW = "from .pages import bioparser_lines_var, model_json_to_pages\n"

_DV_DOCUMENT_OLD = '''def model_json_to_middle_json(model_json: ModelJson) -> MiddleJson:
    """只执行确定性后处理，智能增强由调用方另行执行。"""
    extensions = deepcopy(model_json.extensions)
    return MiddleJson(
        pages=model_json_to_pages(model_json, structure_extension=extensions.get("docvortex_pdf_structure")),
        is_full_document=model_json.is_full_document,
        metadata=model_json.metadata.model_copy(deep=True),
        extensions=extensions,
    )
'''

_DV_DOCUMENT_NEW = '''def model_json_to_middle_json(model_json: ModelJson) -> MiddleJson:
    """只执行确定性后处理，智能增强由调用方另行执行。"""
    extensions = deepcopy(model_json.extensions)
    middle_json = MiddleJson(
        pages=model_json_to_pages(model_json, structure_extension=extensions.get("docvortex_pdf_structure")),
        is_full_document=model_json.is_full_document,
        metadata=model_json.metadata.model_copy(deep=True),
        extensions=extensions,
    )
    harvested = bioparser_lines_var.get()
    if isinstance(harvested, dict) and harvested.get("pages"):
        middle_json.extensions = {**middle_json.extensions, "bioparser_lines": harvested}
    return middle_json
'''


def _replace(path: Path, old: str, new: str, *, already: str) -> None:
    text = path.read_text(encoding="utf-8")
    if already in text:
        return
    if old not in text:
        raise SystemExit(
            f"mineru glyph patch does not match {path}. The installed file is not the pinned release."
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _patch_mineru(site_packages: Path) -> None:
    native = site_packages / "mineru" / "backend" / "analysis" / "pdf" / "text" / "native.py"
    content = site_packages / "mineru" / "backend" / "analysis" / "pdf" / "text" / "content.py"
    if not native.is_file() or not content.is_file():
        raise SystemExit(
            f"mineru glyph patch does not match {native.parent}. The installed MinerU is not 4.2.3."
        )
    _replace(native, _KEEP_OLD, _KEEP_NEW, already="def _bioparser_keep_glyphs")
    _replace(
        native,
        _DELETE_CONTENT_OLD,
        _DELETE_CONTENT_NEW,
        already="_bioparser_keep_glyphs(span)\n    del span.metadata",
    )
    _replace(
        native,
        _DELETE_PREBUILT_OLD,
        _DELETE_PREBUILT_NEW,
        already='_bioparser_keep_glyphs(span)\n            del span.metadata["chars"]',
    )
    _replace(
        native,
        _ASSIGN_OLD,
        _ASSIGN_NEW,
        already="assigned_span_indices = _owned_span_assignments(_native_text, spans, median_span_height)\n",
    )
    _replace(content, _LINE_ITEMS_OLD, _LINE_ITEMS_NEW, already="def _bioparser_glyph")


def _patch_docvortex(site_packages: Path) -> None:
    root = site_packages / "docvortex" / "postprocess"
    pages = root / "pages.py"
    document = root / "document.py"
    page_blocks = root / "page_blocks.py"
    structure = root / "pdf_structure.py"
    if (
        not pages.is_file()
        or not document.is_file()
        or not page_blocks.is_file()
        or not structure.is_file()
    ):
        raise SystemExit(
            f"mineru glyph patch does not match {root}. The installed DocVortex is not 0.6.24."
        )
    _replace(
        pages, _PAGES_IMPORT_OLD, _PAGES_IMPORT_NEW, already="from contextvars import ContextVar"
    )
    _replace(
        pages,
        _PAGES_START_OLD,
        _PAGES_CAPTURE + _PAGES_START_NEW,
        already="def _bioparser_note_flat",
    )
    _replace(
        pages,
        _PAGES_STAMP_OLD,
        _PAGES_STAMP_NEW,
        already="_bioparser_set_page_idx(page_idx)",
    )
    _replace(
        page_blocks,
        _PAGE_BLOCKS_NOTE_OLD,
        _PAGE_BLOCKS_NOTE_NEW,
        already="_bioparser_note_flat(blocks)",
    )
    _replace(
        structure,
        _STRUCTURE_REMAP_OLD,
        _STRUCTURE_REMAP_NEW,
        already="_bioparser_remap_open_page(positions)",
    )
    _replace(
        structure,
        _STRUCTURE_NOTE_OLD,
        _STRUCTURE_NOTE_NEW,
        already="_bioparser_note_flat(trusted)",
    )
    _replace(document, _DV_DOCUMENT_OLD, _DV_DOCUMENT_NEW, already='extensions, "bioparser_lines"')
    _replace(document, _DOC_IMPORT_OLD, _DOC_IMPORT_NEW, already=_DOC_IMPORT_NEW)


_NATIVE_FIELD_OLD = """    _graphic_numeric_features: tuple | None = field(compare=False, repr=False, default=None)

    def __post_init__(self) -> None:
"""

_NATIVE_FIELD_NEW = """    _graphic_numeric_features: tuple | None = field(compare=False, repr=False, default=None)
    bioparser_glyphs: list[dict[str, object]] = field(default_factory=list)

    def __post_init__(self) -> None:
"""

_NATIVE_CLEAR_OLD = """        _freeze_wrapped_bold_title_evidence(line, page_size)
        line.chars.clear()
"""

_NATIVE_CLEAR_NEW = """        _freeze_wrapped_bold_title_evidence(line, page_size)
        _bioparser_freeze_glyphs(line, page_size)
        line.chars.clear()
"""

_NATIVE_OUTPUT_OLD = """        output_block["lines"] = _normalize_output_line_items(block, page_size)
"""

_NATIVE_OUTPUT_NEW = """        output_block["lines"] = _bioparser_lines(block, page_size)
"""

_NATIVE_FREEZE = '''def _bioparser_freeze_glyphs(line: _LineItem, page_size: tuple[float, float]) -> None:
    """Keep each character's horizontal edges. The next step clears ``line.chars``."""
    glyphs: list[dict[str, object]] = []
    for char in line.chars or []:
        if not isinstance(char, dict):
            continue
        text = char.get("char")
        if not isinstance(text, str) or text == "":
            continue
        try:
            raw = tuple(float(value) for value in char.get("bbox"))
        except (TypeError, ValueError):
            continue
        if len(raw) != 4:
            continue
        unit = _normalize_bbox_to_unit(raw, page_size)
        glyphs.append({"t": text, "x0": unit[0], "x1": unit[2]})
    line.bioparser_glyphs = glyphs


def _bioparser_lines(block: dict[str, Any], page_size: tuple[float, float]) -> list[dict[str, Any]]:
    """The public line box, plus the glyph edges frozen before the character list was cleared."""
    items = _normalize_output_line_items(block, page_size)
    members = block.get("_text_lines") or []
    if not isinstance(members, list):
        return items
    for item, member in zip(items, members, strict=False):
        glyphs = getattr(member, "bioparser_glyphs", None)
        item["glyphs"] = glyphs if isinstance(glyphs, list) else []
    return items


'''


_MERGE_COPIES = (
    (
        """            *detected_regions,
        ],
    )
    _inherit_unanimous_structural_membership(merged, ordered_members)
""",
        """            *detected_regions,
        ],
    )
    merged.bioparser_glyphs = [glyph for member in ordered_members for glyph in member.bioparser_glyphs]
    _inherit_unanimous_structural_membership(merged, ordered_members)
""",
        "*detected_regions,\n        ],\n    )\n    merged.bioparser_glyphs",
    ),
    (
        """        inline_math_regions=[region for member in members for region in member.inline_math_regions],
    )
    _inherit_unanimous_structural_membership(merged, members)
""",
        """        inline_math_regions=[region for member in members for region in member.inline_math_regions],
    )
    merged.bioparser_glyphs = [glyph for member in members for glyph in member.bioparser_glyphs]
    _inherit_unanimous_structural_membership(merged, members)
""",
        "region for member in members for region in member.inline_math_regions],\n    )\n    merged.bioparser_glyphs",
    ),
    (
        """        inline_math_regions=[region for member in ordered_members for region in member.inline_math_regions],
    )
    _inherit_unanimous_structural_membership(merged, ordered_members)
""",
        """        inline_math_regions=[region for member in ordered_members for region in member.inline_math_regions],
    )
    merged.bioparser_glyphs = [glyph for member in ordered_members for glyph in member.bioparser_glyphs]
    _inherit_unanimous_structural_membership(merged, ordered_members)
""",
        "region for member in ordered_members for region in member.inline_math_regions],\n    )\n    merged.bioparser_glyphs",
    ),
)


def _patch_docvortex_native(site_packages: Path) -> None:
    root = site_packages / "docvortex" / "analyzers" / "native" / "pdf"
    models = root / "models.py"
    pipeline = root / "pipeline.py"
    merging = root / "line_merging.py"
    if not models.is_file() or not pipeline.is_file() or not merging.is_file():
        raise SystemExit(
            f"mineru glyph patch does not match {root}. The installed DocVortex is not 0.6.24."
        )
    _replace(models, _NATIVE_FIELD_OLD, _NATIVE_FIELD_NEW, already="bioparser_glyphs")
    _replace(
        pipeline,
        "def _compact_prepared_lines(\n",
        _NATIVE_FREEZE + "def _compact_prepared_lines(\n",
        already="def _bioparser_freeze_glyphs",
    )
    _replace(
        pipeline,
        _NATIVE_CLEAR_OLD,
        _NATIVE_CLEAR_NEW,
        already="_bioparser_freeze_glyphs(line, page_size)",
    )
    _replace(
        pipeline,
        _NATIVE_OUTPUT_OLD,
        _NATIVE_OUTPUT_NEW,
        already="_bioparser_lines(block, page_size)",
    )
    for old, new, already in _MERGE_COPIES:
        _replace(merging, old, new, already=already)


def apply(site_packages: Path) -> None:
    _patch_mineru(site_packages)
    _patch_docvortex(site_packages)
    _patch_docvortex_native(site_packages)


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve()
    if len(sys.argv) == 1:
        import mineru  # type: ignore[import-not-found]

        root = Path(mineru.__file__).resolve().parent.parent
    apply(root)


if __name__ == "__main__":
    main()
