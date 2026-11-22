"""Copy MinerU 4.2.3 character boxes out before they are deleted.

MinerU puts each native character on ``span.metadata["chars"]``, turns that list into the
span text, and then deletes it. Basic and above can rebuild that text in Rust and skip
the character list. The line builder runs after the delete, so a line box is
all that remains. DocVortex 0.6.24 then deletes ``lines`` while it builds the public
document. This rewrites those spots so the middle document gains
``extensions.bioparser_lines``. MinerU can rebuild text without character boxes, and the
assigner still leaves some spans empty. Those spans contribute no glyphs. Flash OCR has
no character list from the detector. The recognizer already has a column for each
character, and this keeps that column and places it along the detection box. Tagged
text keeps one box for the whole block. Each character box is copied onto the visual
line it belongs to. It refuses to edit a file that does not still contain the functions
it was written against.
"""

from __future__ import annotations

import inspect
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
            for char in saved:
                glyph = _bioparser_glyph(char, page_size)
                if glyph is not None:
                    glyphs.append(glyph)
        line_items.append({"bbox": normalized_bbox, "glyphs": glyphs})
    return line_items
'''

# A tree patched before recognizer columns still even-splits an empty span. Drop that.
_SPAN_BOX_OLD = """            saved = span.metadata.get("bioparser_glyphs") if isinstance(span.metadata, dict) else None
            if isinstance(saved, list) and saved:
                for char in saved:
                    glyph = _bioparser_glyph(char, page_size)
                    if glyph is not None:
                        glyphs.append(glyph)
                continue
            # An empty list is a native span the assigner did not fill. Flash OCR never
            # writes the list: the detected line is one text span and one box.
            if not isinstance(saved, list) and (
                saved is not None or getattr(span, "type", None) != ContentType.TEXT
            ):
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
"""

_SPAN_BOX_NEW = """            saved = span.metadata.get("bioparser_glyphs") if isinstance(span.metadata, dict) else None
            if not isinstance(saved, list):
                continue
            for char in saved:
                glyph = _bioparser_glyph(char, page_size)
                if glyph is not None:
                    glyphs.append(glyph)
"""

_OCR_SPAN_OLD = """        page_spans.append(
            _AnalyzeSpan(
                type=ContentType.TEXT,
                bbox=bbox,
                content=str(ocr_res.get("text", "") or ""),
                score=float(ocr_res.get("score", 0.0) or 0.0),
            )
        )
"""

_OCR_SPAN_NEW = """        span = _AnalyzeSpan(
            type=ContentType.TEXT,
            bbox=bbox,
            content=str(ocr_res.get("text", "") or ""),
            score=float(ocr_res.get("score", 0.0) or 0.0),
        )
        raw_chars = ocr_res.get("bioparser_chars") if isinstance(ocr_res, dict) else None
        if isinstance(raw_chars, list) and raw_chars:
            glyphs: list[dict[str, object]] = []
            for char in raw_chars:
                if not isinstance(char, dict):
                    glyphs = []
                    break
                page_box = _sidecar_bbox_to_page_bbox(char.get("bbox"), page_size, render_scale)
                char_text = char.get("char")
                if page_box is None or not isinstance(char_text, str) or char_text == "":
                    glyphs = []
                    break
                glyphs.append({"char": char_text, "bbox": list(page_box)})
            if glyphs:
                span.metadata["bioparser_glyphs"] = glyphs
        page_spans.append(span)
"""

_DECODE_OLD = """            sequence = sequence[final_mask]
            probs = None if probs is None else probs[final_mask]
            text = "".join(self.character[sequence])

            if text_prob is not None and probs is not None and len(probs) > 0:
                mean_conf = np.mean(probs)
            else:
                # 如果没有提供概率或最终结果为空，则默认置信度为1.0
                mean_conf = 1.0
            result_list.append((text, mean_conf))
"""

_DECODE_NEW = """            column_count = int(text_index[batch_idx].shape[0])
            kept = np.flatnonzero(final_mask)
            centers: list[float] = []
            if return_word_box:
                raw = text_index[batch_idx]
                for index in kept:
                    token = raw[index]
                    end = int(index) + 1
                    while end < column_count and raw[end] == token:
                        end += 1
                    centers.append((int(index) + end - 1) / 2)

            sequence = sequence[final_mask]
            probs = None if probs is None else probs[final_mask]
            text = "".join(self.character[sequence])

            if text_prob is not None and probs is not None and len(probs) > 0:
                mean_conf = np.mean(probs)
            else:
                # 如果没有提供概率或最终结果为空，则默认置信度为1.0
                mean_conf = 1.0
            if return_word_box:
                result_list.append((text, mean_conf, [column_count, centers]))
            else:
                result_list.append((text, mean_conf))
"""

_REC_CALL_OLD = """    def __call__(self, img_list, tqdm_enable=False, tqdm_desc="OCR-rec Predict", tqdm_progress_bar=None):
"""

_REC_CALL_NEW = """    def __call__(self, img_list, tqdm_enable=False, tqdm_desc="OCR-rec Predict", tqdm_progress_bar=None, return_word_box=False):
"""

_REC_POST_OLD = """                with torch.inference_mode():
                    rec_result = self.postprocess_op(preds)
"""

_REC_POST_NEW = """                with torch.inference_mode():
                    if return_word_box:
                        wh_ratio_list = [
                            width_list[indices[ino]] for ino in range(beg_img_no, end_img_no)
                        ]
                        rec_result = self.postprocess_op(
                            preds,
                            return_word_box=True,
                            wh_ratio_list=wh_ratio_list,
                            max_wh_ratio=max_wh_ratio,
                        )
                    else:
                        rec_result = self.postprocess_op(preds)
"""

_REC_NAN_OLD = """        for i in range(len(rec_res)):
            text, score = rec_res[i]
            if isinstance(score, float) and math.isnan(score):
                rec_res[i] = (text, 0.0)
"""

_REC_NAN_NEW = """        for i in range(len(rec_res)):
            item = rec_res[i]
            score = item[1]
            if isinstance(score, float) and math.isnan(score):
                rec_res[i] = (item[0], 0.0, *item[2:])
"""

_PADDLE_REC_OLD = """                    rec_res, elapse = self.text_recognizer(
                        img,
                        tqdm_enable=tqdm_enable,
                        tqdm_desc=tqdm_desc,
                        tqdm_progress_bar=tqdm_progress_bar,
                    )
"""

_PADDLE_REC_NEW = """                    rec_res, elapse = self.text_recognizer(
                        img,
                        tqdm_enable=tqdm_enable,
                        tqdm_desc=tqdm_desc,
                        tqdm_progress_bar=tqdm_progress_bar,
                        return_word_box=True,
                    )
"""

_OCR_IMPORT_OLD = """from ....model.ocr.image import mask_formula_regions_for_ocr_det, rgb_to_bgr
"""

_OCR_IMPORT_NEW = """from ....model.ocr.image import TEXT_REC_ROTATE_RATIO, mask_formula_regions_for_ocr_det, rgb_to_bgr
"""


def _bioparser_ocr_chars(
    text: str,
    info: object,
    bbox: object,
    rotate_ratio: float = 1.5,
) -> list[dict[str, object]] | None:
    """Place each recognized character along a horizontal detection box.

    ``info`` is the column count after padding correction, then one column center
    per character. A vertical crop is left alone: its columns run down the page.
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
    if (
        len(columns) != len(text)
        or column_count <= 0
        or width <= 0
        or height <= 0
        or height / width >= rotate_ratio
    ):
        return None
    centers = [min(max(column / column_count, 0.0), 1.0) for column in columns]
    bounds = [0.0]
    previous: float | None = None
    for center in centers:
        if previous is not None:
            bounds.append((previous + center) / 2)
        previous = center
    bounds.append(1.0)
    chars: list[dict[str, object]] = []
    for char, left, right in zip(text, bounds[:-1], bounds[1:], strict=True):
        if right <= left:
            return None
        chars.append({"char": char, "bbox": [x0 + left * width, y0, x0 + right * width, y1]})
    return chars


_OCR_CHARS_FN = inspect.getsource(_bioparser_ocr_chars)

_OCR_FN_OLD = """def _apply_ocr_rec_results(
"""

_OCR_FN_NEW = (
    _OCR_CHARS_FN
    + "\n\n"
    + """def _apply_ocr_rec_results(
"""
)

_OCR_APPLY_OLD = """    items_to_remove = []
    for index, (page_ocr_res_list, need_ocr_res) in enumerate(need_ocr_list):
        ocr_text, ocr_score = ocr_result_list[index]
        need_ocr_res["text"] = ocr_text
        need_ocr_res["score"] = float(f"{ocr_score:.3f}")
"""

_OCR_APPLY_NEW = """    items_to_remove = []
    for index, (page_ocr_res_list, need_ocr_res) in enumerate(need_ocr_list):
        ocr_result = ocr_result_list[index]
        ocr_text, ocr_score = ocr_result[0], ocr_result[1]
        need_ocr_res["text"] = ocr_text
        need_ocr_res["score"] = float(f"{ocr_score:.3f}")
        info = ocr_result[2] if len(ocr_result) > 2 else None
        chars = _bioparser_ocr_chars(
            ocr_text, info, need_ocr_res.get("bbox"), TEXT_REC_ROTATE_RATIO
        )
        if chars is not None:
            need_ocr_res["bioparser_chars"] = chars
"""

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


def _replace_present(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        return
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


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
    _replace_present(content, _SPAN_BOX_OLD, _SPAN_BOX_NEW)
    _replace(content, _OCR_SPAN_OLD, _OCR_SPAN_NEW, already='ocr_res.get("bioparser_chars")')
    rec_post = (
        site_packages
        / "mineru"
        / "model"
        / "_internal"
        / "pytorchocr"
        / "postprocess"
        / "rec_postprocess.py"
    )
    predict_rec = (
        site_packages / "mineru" / "model" / "_internal" / "pytorchocr" / "infer" / "predict_rec.py"
    )
    paddle_ocr = site_packages / "mineru" / "model" / "ocr" / "pytorch_paddle.py"
    ocr = site_packages / "mineru" / "backend" / "analysis" / "pdf" / "ocr.py"
    _replace(
        rec_post,
        _DECODE_OLD,
        _DECODE_NEW,
        already="result_list.append((text, mean_conf, [column_count, centers]))",
    )
    _replace(predict_rec, _REC_CALL_OLD, _REC_CALL_NEW, already="return_word_box=False")
    _replace(predict_rec, _REC_POST_OLD, _REC_POST_NEW, already="wh_ratio_list=wh_ratio_list")
    _replace(predict_rec, _REC_NAN_OLD, _REC_NAN_NEW, already="item = rec_res[i]")
    _replace(paddle_ocr, _PADDLE_REC_OLD, _PADDLE_REC_NEW, already="return_word_box=True")
    _replace(ocr, _OCR_IMPORT_OLD, _OCR_IMPORT_NEW, already="TEXT_REC_ROTATE_RATIO")
    _replace(ocr, _OCR_FN_OLD, _OCR_FN_NEW, already="def _bioparser_ocr_chars")
    _replace(ocr, _OCR_APPLY_OLD, _OCR_APPLY_NEW, already='need_ocr_res["bioparser_chars"]')


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


def _bioparser_tagged_lines(chars: object, page_size: object) -> list[dict[str, object]]:
    """One normalized line per visual run, with each character's own horizontal edges.

    Characters stay in the order the structure tree produced. A character joins the
    current line when their boxes overlap vertically. The next run starts its own line,
    so a paragraph is not one tall box.
    """
    try:
        width = float(page_size[0])  # type: ignore[index]
        height = float(page_size[1])  # type: ignore[index]
    except (TypeError, ValueError, IndexError):
        return []
    if width <= 0 or height <= 0 or not isinstance(chars, list):
        return []
    pending: list[tuple[str, tuple[float, float, float, float]]] = []
    for char in chars:
        if not isinstance(char, dict) or char.get("text_is_visible") is False:
            continue
        text = char.get("char")
        if not isinstance(text, str):
            continue
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if text == "" or text == "\x00" or "\n" in text:
            continue
        try:
            raw = tuple(float(value) for value in char.get("bbox"))
        except (TypeError, ValueError):
            continue
        if len(raw) != 4 or raw[2] <= raw[0] or raw[3] <= raw[1]:
            continue
        pending.append((text, (raw[0], raw[1], raw[2], raw[3])))
    groups: list[list[int]] = []
    bounds: list[list[float]] = []
    for index, (_text, raw) in enumerate(pending):
        top, bottom = raw[1], raw[3]
        chosen: int | None = None
        if bounds:
            line_top, line_bottom = bounds[-1]
            overlap = min(bottom, line_bottom) - max(top, line_top)
            limit = min(bottom - top, line_bottom - line_top)
            if limit > 0 and overlap >= 0.4 * limit:
                chosen = len(bounds) - 1
        if chosen is None:
            groups.append([index])
            bounds.append([top, bottom])
        else:
            groups[chosen].append(index)
            bounds[chosen][0] = min(bounds[chosen][0], top)
            bounds[chosen][1] = max(bounds[chosen][1], bottom)
    lines: list[dict[str, object]] = []
    for group in groups:
        glyphs: list[dict[str, object]] = []
        lefts: list[float] = []
        tops: list[float] = []
        rights: list[float] = []
        bottoms: list[float] = []
        for index in group:
            text, raw = pending[index]
            left = min(max(raw[0] / width, 0.0), 1.0)
            right = min(max(raw[2] / width, 0.0), 1.0)
            top = min(max(raw[1] / height, 0.0), 1.0)
            bottom = min(max(raw[3] / height, 0.0), 1.0)
            if right <= left or bottom <= top:
                continue
            glyphs.append({"t": text, "x0": left, "x1": right})
            lefts.append(left)
            tops.append(top)
            rights.append(right)
            bottoms.append(bottom)
        if glyphs:
            lines.append(
                {
                    "bbox": [min(lefts), min(tops), max(rights), max(bottoms)],
                    "glyphs": glyphs,
                }
            )
    return lines


_TAGGED_FN = inspect.getsource(_bioparser_tagged_lines)

_TAGGED_CLASS_OLD = "class _PageAssembler:\n"

_TAGGED_CLASS_NEW = _TAGGED_FN + "\n\nclass _PageAssembler:\n"

_TAGGED_CALL_OLD = """            block["lines"] = [{"bbox": box}]
"""

_TAGGED_CALL_NEW = """            measured: list[dict[str, object]] = []
            if node.actual_text is None:
                measured = [
                    char
                    for obj in members
                    for char in getattr(obj, "chars", ())
                    if isinstance(char, dict) and char.get("char_idx") not in self.suppressed_spaces
                ]
            block["lines"] = _bioparser_tagged_lines(measured, self.page.size) or [{"bbox": box}]
"""


def _patch_docvortex_native(site_packages: Path) -> None:
    root = site_packages / "docvortex" / "analyzers" / "native" / "pdf"
    models = root / "models.py"
    pipeline = root / "pipeline.py"
    merging = root / "line_merging.py"
    tagged = root / "tagged.py"
    if (
        not models.is_file()
        or not pipeline.is_file()
        or not merging.is_file()
        or not tagged.is_file()
    ):
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
    _replace(tagged, _TAGGED_CLASS_OLD, _TAGGED_CLASS_NEW, already="def _bioparser_tagged_lines")
    _replace(
        tagged,
        _TAGGED_CALL_OLD,
        _TAGGED_CALL_NEW,
        already="_bioparser_tagged_lines(measured, self.page.size)",
    )


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
