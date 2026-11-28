"""Keep the glyph edges MinerU 4.2.3 and DocVortex 0.6.24 measure, then throw away.

MinerU puts each native character on ``span.metadata["chars"]``, builds the span text
from it, and deletes it. DocVortex deletes ``lines`` while it builds the public document.
BioParser needs per-character boxes for search highlights, so these hooks carry them
through to ``extensions.bioparser_lines``. All behavior lives in ``bioparser_glyphs.py``.

Hooks are listed in the order the data moves, in four groups: native PDF text, OCR,
DocVortex pages, and DocVortex native lines. See ``bioparser_glyphs`` for the data flow.
"""

from __future__ import annotations

from pathlib import Path

from lib.hooks import Feature, Hook
from versions import DOCVORTEX_VERSION, MINERU_VERSION

_ANALYSIS = "mineru/backend/analysis/pdf"
_NATIVE = f"{_ANALYSIS}/text/native.py"
_CONTENT = f"{_ANALYSIS}/text/content.py"
_OCR = f"{_ANALYSIS}/ocr.py"
_FORMULAS = f"{_ANALYSIS}/formulas.py"
_RECOGNIZER = "mineru/model/_internal/pytorchocr/infer/predict_rec.py"
_DECODE = "mineru/model/_internal/pytorchocr/postprocess/rec_postprocess.py"
_PADDLE = "mineru/model/ocr/pytorch_paddle.py"
_ONNX = "mineru/model/ocr/pp_ocr_v6_onnx.py"
_UNET = "mineru/model/table/rec/unet_table/main.py"
_TABLE_ORI = "mineru/model/table/cls/mineru_table_ori_cls.py"

_PAGES = "docvortex/postprocess/pages.py"
_DOCUMENT = "docvortex/postprocess/document.py"
_PAGE_BLOCKS = "docvortex/postprocess/page_blocks.py"
_STRUCTURE = "docvortex/postprocess/pdf_structure.py"
_MODELS = "docvortex/analyzers/native/pdf/models.py"
_PIPELINE = "docvortex/analyzers/native/pdf/pipeline.py"
_MERGING = "docvortex/analyzers/native/pdf/line_merging.py"
_TAGGED = "docvortex/analyzers/native/pdf/tagged.py"


# -- 1. Native PDF text: copy character boxes before ``chars`` is deleted ----------------

NATIVE_HOOKS = (
    Hook(
        _NATIVE,
        """        if any(role in {"sup", "sub"} and text for role, text in role_text_parts):
            span.metadata[PDF_NATIVE_SCRIPT_MARKUP_KEY] = True

    del span.metadata["chars"]
""",
        """        if any(role in {"sup", "sub"} and text for role, text in role_text_parts):
            span.metadata[PDF_NATIVE_SCRIPT_MARKUP_KEY] = True

    bioparser_glyphs.keep_span_glyphs(span)
    del span.metadata["chars"]
""",
    ),
    # Basic and above rebuild the text in Rust and take this path instead.
    Hook(
        _NATIVE,
        """            if text is not None:
                span.content = text
            del span.metadata["chars"]
""",
        """            if text is not None:
                span.content = text
            bioparser_glyphs.keep_span_glyphs(span)
            del span.metadata["chars"]
""",
    ),
    # The assigner skipped spans when MinerU rebuilt the text without character boxes.
    Hook(
        _NATIVE,
        """    assigned_span_indices = _owned_span_assignments(_native_text, spans, median_span_height) if native_contents is None else []
""",
        """    assigned_span_indices = _owned_span_assignments(_native_text, spans, median_span_height)
""",
    ),
    # The line builder runs after the delete. It reads the saved glyphs off each span.
    Hook(
        _CONTENT,
        '''def _build_ocr_det_line_items(lines: list[_AnalyzeLine], page_size: tuple[float, float]) -> list[dict[str, Any]]:
    """将 Analyze 私有行转换为归一化行框。"""
    line_items = []
    for line in lines:
        normalized_bbox = _page_bbox_to_unit_bbox(line.bbox, page_size)
        if normalized_bbox is not None:
            line_items.append({"bbox": normalized_bbox})
    return line_items
''',
        '''def _build_ocr_det_line_items(lines: list[_AnalyzeLine], page_size: tuple[float, float]) -> list[dict[str, Any]]:
    """将 Analyze 私有行转换为归一化行框，并保留每个字形的水平边缘。"""
    line_items = []
    for line in lines:
        normalized_bbox = _page_bbox_to_unit_bbox(line.bbox, page_size)
        if normalized_bbox is None:
            continue
        glyphs = bioparser_glyphs.line_glyphs(
            line.spans,
            lambda box: _page_bbox_to_unit_bbox(box, page_size),
            ContentType.INLINE_EQUATION,
        )
        line_items.append({"bbox": normalized_bbox, "glyphs": glyphs})
    return line_items
''',
    ),
    # A measured line is kept on every block. The mapper decides which block types stay.
    Hook(
        _CONTENT,
        """            block_item[PDF_NATIVE_SCRIPT_MARKUP_KEY] = True

        if block_type in LINE_METADATA_BLOCK_TYPES:
            block_item["lines"] = _build_ocr_det_line_items(lines, page_size)
""",
        """            block_item[PDF_NATIVE_SCRIPT_MARKUP_KEY] = True

        block_item["lines"] = _build_ocr_det_line_items(lines, page_size)
""",
    ),
    Hook(
        _CONTENT,
        """    target_block_types = set(ocr_det_type) | TITLE_BLOCK_TYPES | {BlockType.TEXT}
""",
        """    target_block_types = set(ocr_det_type) | TITLE_BLOCK_TYPES | {BlockType.TEXT, BlockType.PAGE_FOOTNOTE}
""",
    ),
    # A page footnote is a target only for its measured lines. Its stored content stays.
    Hook(
        _CONTENT,
        """        if not has_nonempty_content:
            block_item["content"] = _lines_to_block_content(lines, block_type)
""",
        """        if not has_nonempty_content and block_type != BlockType.PAGE_FOOTNOTE:
            block_item["content"] = _lines_to_block_content(lines, block_type)
""",
    ),
)

# -- 2. OCR: keep the recognizer's columns and place each character along the box --------
#
# Flash OCR has no character list from the detector, but the recognizer already has a
# column for each character. The recognizer returns ``(text, score, [columns, centers])``
# once ``return_word_box`` is on, so the callers that unpack two values take two items.

OCR_HOOKS = (
    # Recognizer: return the column count and centers next to text and score.
    Hook(
        _DECODE,
        """            sequence = sequence[final_mask]
            probs = None if probs is None else probs[final_mask]
            text = "".join(self.character[sequence])

            if text_prob is not None and probs is not None and len(probs) > 0:
                mean_conf = np.mean(probs)
            else:
                # 如果没有提供概率或最终结果为空，则默认置信度为1.0
                mean_conf = 1.0
            result_list.append((text, mean_conf))
""",
        """            column_count = int(text_index[batch_idx].shape[0])
            centers = (
                bioparser_glyphs.column_centers(text_index[batch_idx], np.flatnonzero(final_mask))
                if return_word_box
                else []
            )

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
""",
    ),
    Hook(
        _RECOGNIZER,
        """    def __call__(self, img_list, tqdm_enable=False, tqdm_desc="OCR-rec Predict", tqdm_progress_bar=None):
""",
        """    def __call__(self, img_list, tqdm_enable=False, tqdm_desc="OCR-rec Predict", tqdm_progress_bar=None, return_word_box=False):
""",
    ),
    Hook(
        _RECOGNIZER,
        """                with torch.inference_mode():
                    rec_result = self.postprocess_op(preds)
""",
        """                with torch.inference_mode():
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
""",
    ),
    Hook(
        _RECOGNIZER,
        """        for i in range(len(rec_res)):
            text, score = rec_res[i]
            if isinstance(score, float) and math.isnan(score):
                rec_res[i] = (text, 0.0)
""",
        """        for i in range(len(rec_res)):
            item = rec_res[i]
            score = item[1]
            if isinstance(score, float) and math.isnan(score):
                rec_res[i] = (item[0], 0.0, *item[2:])
""",
    ),
    Hook(
        _PADDLE,
        """                    rec_res, elapse = self.text_recognizer(
                        img,
                        tqdm_enable=tqdm_enable,
                        tqdm_desc=tqdm_desc,
                        tqdm_progress_bar=tqdm_progress_bar,
                    )
""",
        """                    rec_res, elapse = self.text_recognizer(
                        img,
                        tqdm_enable=tqdm_enable,
                        tqdm_desc=tqdm_desc,
                        tqdm_progress_bar=tqdm_progress_bar,
                        return_word_box=True,
                    )
""",
    ),
    # The ONNX recognizer decodes in its own class.
    Hook(
        _ONNX,
        '''    def _decode(self, idx: np.ndarray, prob: np.ndarray) -> Tuple[str, float]:
        """解码已在 ONNX 图内归约的 ID/置信度，保留 CTC 和空行语义。"""
        # collapse consecutive duplicates
        selection = np.ones(len(idx), dtype=bool)
        selection[1:] = idx[1:] != idx[:-1]
        # remove blank (index 0)
        selection &= idx != 0
        chars = [self.character[i] for i in idx[selection]]
        text = "".join(chars)
        conf = float(prob[selection].mean()) if selection.any() else 1.0
        return text, conf
''',
        '''    def _decode(
        self,
        idx: np.ndarray,
        prob: np.ndarray,
        return_word_box: bool = False,
    ) -> tuple[str, float] | tuple[str, float, int, list[float]]:
        """解码已在 ONNX 图内归约的 ID/置信度，保留 CTC 和空行语义。"""
        # collapse consecutive duplicates
        selection = np.ones(len(idx), dtype=bool)
        selection[1:] = idx[1:] != idx[:-1]
        # remove blank (index 0)
        selection &= idx != 0
        chars = [self.character[i] for i in idx[selection]]
        text = "".join(chars)
        conf = float(prob[selection].mean()) if selection.any() else 1.0
        if not return_word_box:
            return text, conf
        centers = bioparser_glyphs.column_centers(idx, np.flatnonzero(selection))
        return text, conf, int(len(idx)), centers
''',
    ),
    Hook(
        _ONNX,
        """        tqdm_progress_bar: Optional[Any] = None,
    ) -> Tuple[List[Tuple[str, float]], float]:
""",
        """        tqdm_progress_bar: Optional[Any] = None,
        return_word_box: bool = False,
    ) -> Tuple[List[Tuple[str, float]], float]:
""",
    ),
    Hook(
        _ONNX,
        """                for i, idx in enumerate(batch_indices):
                    text, conf = self._decode(ids[i], scores[i])
                    rec_res[idx] = (text, conf)
""",
        """                for i, idx in enumerate(batch_indices):
                    decoded = self._decode(
                        ids[i], scores[i], return_word_box=return_word_box
                    )
                    if return_word_box:
                        text, conf, column_count, centers = decoded
                        scale = width_list[idx] / max_wh_ratio if max_wh_ratio > 0 else 1.0
                        rec_res[idx] = (text, conf, [column_count * scale, centers])
                    else:
                        text, conf = decoded
                        rec_res[idx] = (text, conf)
""",
    ),
    Hook(
        _ONNX,
        """                    rec_res, _elapse = self.text_recognizer(
                        img,
                        tqdm_enable=tqdm_enable,
                        tqdm_desc=tqdm_desc,
                        tqdm_progress_bar=tqdm_progress_bar,
                    )
""",
        """                    rec_res, _elapse = self.text_recognizer(
                        img,
                        tqdm_enable=tqdm_enable,
                        tqdm_desc=tqdm_desc,
                        tqdm_progress_bar=tqdm_progress_bar,
                        return_word_box=True,
                    )
""",
    ),
    # Callers that unpacked ``(text, score)`` now read the first two items.
    Hook(
        _FORMULAS,
        """        ocr_text, _ = ocr_result
        normalized_text = _normalize_medium_content(ocr_text)
""",
        """        ocr_text = ocr_result[0]
        normalized_text = _normalize_medium_content(ocr_text)
""",
    ),
    Hook(
        _UNET,
        """            for i, box, ocr_res in img_crop_info_list:
                # 处理ocr结果
                ocr_text, ocr_score = ocr_res
""",
        """            for i, box, ocr_res in img_crop_info_list:
                # 处理ocr结果
                ocr_text, ocr_score = ocr_res[0], ocr_res[1]
""",
    ),
    Hook(
        _TABLE_ORI,
        """            if not rec_item or len(rec_item) < 2:
                continue
            text, score = rec_item
""",
        """            if not rec_item or len(rec_item) < 2:
                continue
            text, score = rec_item[0], rec_item[1]
""",
    ),
    # Recognized text in a span: place the characters on the span's own box.
    Hook(
        _CONTENT,
        """    for span, ocr_res in zip(need_ocr_spans, ocr_res_list):
        ocr_text, ocr_score = ocr_res
        if ocr_score > OcrConfidence.min_confidence:
            span.content = ocr_text
            span.score = float(f"{ocr_score:.3f}")
            _clear_post_ocr_fallback(span)
""",
        """    for span, ocr_res in zip(need_ocr_spans, ocr_res_list):
        ocr_text, ocr_score = ocr_res[0], ocr_res[1]
        if ocr_score > OcrConfidence.min_confidence:
            span.content = ocr_text
            span.score = float(f"{ocr_score:.3f}")
            info = ocr_res[2] if len(ocr_res) > 2 else None
            from .....model.ocr.image import TEXT_REC_ROTATE_RATIO

            chars = bioparser_glyphs.ocr_chars(ocr_text, info, span.bbox, TEXT_REC_ROTATE_RATIO)
            if chars is not None:
                span.metadata["bioparser_glyphs"] = chars
            _clear_post_ocr_fallback(span)
""",
    ),
    # Recognized text on a detection box: place the characters, pass them on as a sidecar.
    Hook(
        _OCR,
        """from ....model.ocr.image import mask_formula_regions_for_ocr_det, rgb_to_bgr
""",
        """from ....model.ocr.image import TEXT_REC_ROTATE_RATIO, mask_formula_regions_for_ocr_det, rgb_to_bgr
""",
    ),
    Hook(
        _OCR,
        """    items_to_remove = []
    for index, (page_ocr_res_list, need_ocr_res) in enumerate(need_ocr_list):
        ocr_text, ocr_score = ocr_result_list[index]
        need_ocr_res["text"] = ocr_text
        need_ocr_res["score"] = float(f"{ocr_score:.3f}")
""",
        """    items_to_remove = []
    for index, (page_ocr_res_list, need_ocr_res) in enumerate(need_ocr_list):
        ocr_result = ocr_result_list[index]
        ocr_text, ocr_score = ocr_result[0], ocr_result[1]
        need_ocr_res["text"] = ocr_text
        need_ocr_res["score"] = float(f"{ocr_score:.3f}")
        info = ocr_result[2] if len(ocr_result) > 2 else None
        chars = bioparser_glyphs.ocr_chars(
            ocr_text, info, need_ocr_res.get("bbox"), TEXT_REC_ROTATE_RATIO
        )
        if chars is not None:
            need_ocr_res["bioparser_chars"] = chars
""",
    ),
    # The sidecar becomes the glyphs of the new span.
    Hook(
        _CONTENT,
        """        page_spans.append(
            _AnalyzeSpan(
                type=ContentType.TEXT,
                bbox=bbox,
                content=str(ocr_res.get("text", "") or ""),
                score=float(ocr_res.get("score", 0.0) or 0.0),
            )
        )
""",
        """        span = _AnalyzeSpan(
            type=ContentType.TEXT,
            bbox=bbox,
            content=str(ocr_res.get("text", "") or ""),
            score=float(ocr_res.get("score", 0.0) or 0.0),
        )
        glyphs = bioparser_glyphs.sidecar_glyphs(
            ocr_res.get("bioparser_chars") if isinstance(ocr_res, dict) else None,
            lambda box: _sidecar_bbox_to_page_bbox(box, page_size, render_scale),
        )
        if glyphs:
            span.metadata["bioparser_glyphs"] = glyphs
        page_spans.append(span)
""",
    ),
)

# -- 3. DocVortex pages: carry the lines past the step that deletes them -----------------
#
# A page is a flat list of blocks, then gets its ``page_idx`` and its final indices.
# ``bioparser_glyphs`` records the lines on the flat list and follows those renumberings.

PAGES_HOOKS = (
    Hook(
        _PAGES,
        '''def model_json_to_pages(model_json: ModelJson, *, structure_extension: dict | None = None) -> list[PageInfo]:
    """从严格 ModelJson 无副作用地构造可递归序列化的 PageInfo。"""
    page_indices = model_json.resolved_page_indices
''',
        '''def model_json_to_pages(model_json: ModelJson, *, structure_extension: dict | None = None) -> list[PageInfo]:
    """从严格 ModelJson 无副作用地构造可递归序列化的 PageInfo。"""
    bioparser_glyphs.start_capture()
    page_indices = model_json.resolved_page_indices
''',
    ),
    Hook(
        _PAGE_BLOCKS,
        """    blocks = _normalize_raw_blocks(page_model_list)
    _apply_caption_fallbacks(blocks, use_bbox=resolved_use_bbox)
""",
        """    blocks = _normalize_raw_blocks(page_model_list)
    bioparser_glyphs.note_flat(blocks)
    _apply_caption_fallbacks(blocks, use_bbox=resolved_use_bbox)
""",
    ),
    # Tagged pages renumber the flat list after that snapshot.
    Hook(
        _STRUCTURE,
        """    for block in output:
        remap(block)
    trusted = [finalize_structure_block(block, index) for index, block in enumerate(page_model_list) if STRUCTURE_KEY in block]
""",
        """    for block in output:
        remap(block)
    bioparser_glyphs.remap_open_page(positions)
    trusted = [finalize_structure_block(block, index) for index, block in enumerate(page_model_list) if STRUCTURE_KEY in block]
""",
    ),
    Hook(
        _STRUCTURE,
        """            output.append(block)
    return output
""",
        """            output.append(block)
    bioparser_glyphs.note_flat(trusted)
    return output
""",
    ),
    Hook(
        _PAGES,
        """    page_blocks = process_page_blocks(page_model_list, use_bbox=use_bbox)
    page_blocks.sort(key=lambda block: block["index"])
""",
        """    page_blocks = process_page_blocks(page_model_list, use_bbox=use_bbox)
    bioparser_glyphs.set_page_idx(page_idx)
    page_blocks.sort(key=lambda block: block["index"])
""",
    ),
    Hook(
        _DOCUMENT,
        '''def model_json_to_middle_json(model_json: ModelJson) -> MiddleJson:
    """只执行确定性后处理，智能增强由调用方另行执行。"""
    extensions = deepcopy(model_json.extensions)
    return MiddleJson(
        pages=model_json_to_pages(model_json, structure_extension=extensions.get("docvortex_pdf_structure")),
        is_full_document=model_json.is_full_document,
        metadata=model_json.metadata.model_copy(deep=True),
        extensions=extensions,
    )
''',
        '''def model_json_to_middle_json(model_json: ModelJson) -> MiddleJson:
    """只执行确定性后处理，智能增强由调用方另行执行。"""
    extensions = deepcopy(model_json.extensions)
    return bioparser_glyphs.attach_lines(
        MiddleJson(
            pages=model_json_to_pages(model_json, structure_extension=extensions.get("docvortex_pdf_structure")),
            is_full_document=model_json.is_full_document,
            metadata=model_json.metadata.model_copy(deep=True),
            extensions=extensions,
        )
    )
''',
    ),
)

# -- 4. DocVortex native lines: freeze edges, merge them, and keep tagged lines ----------


def _merged_glyphs(old: str, new: str, members: str) -> Hook:
    """A merged line is built from members. Concatenate their glyphs on the result."""
    copy = f"    merged.bioparser_glyphs = [glyph for member in {members} for glyph in member.bioparser_glyphs]\n"
    return Hook(_MERGING, old, new.replace("@GLYPHS@", copy))


NATIVE_LINE_HOOKS = (
    Hook(
        _MODELS,
        """    _graphic_numeric_features: tuple | None = field(compare=False, repr=False, default=None)

    def __post_init__(self) -> None:
""",
        """    _graphic_numeric_features: tuple | None = field(compare=False, repr=False, default=None)
    bioparser_glyphs: list[dict[str, object]] = field(default_factory=list)

    def __post_init__(self) -> None:
""",
    ),
    Hook(
        _PIPELINE,
        """        _freeze_wrapped_bold_title_evidence(line, page_size)
        line.chars.clear()
""",
        """        _freeze_wrapped_bold_title_evidence(line, page_size)
        line.bioparser_glyphs = bioparser_glyphs.char_edges(
            line.chars, lambda box: _normalize_bbox_to_unit(box, page_size)
        )
        line.chars.clear()
""",
    ),
    Hook(
        _PIPELINE,
        """    if normalized_type in _LINE_METADATA_OUTPUT_TYPES:
        output_block["lines"] = _normalize_output_line_items(block, page_size)
""",
        """    lines = bioparser_glyphs.output_lines(
        _normalize_output_line_items(block, page_size), block.get("_text_lines")
    )
    if lines or normalized_type in _LINE_METADATA_OUTPUT_TYPES:
        output_block["lines"] = lines
""",
    ),
    # Three places build a merged line. Each one keeps its members' glyphs.
    _merged_glyphs(
        """            *detected_regions,
        ],
    )
    _inherit_unanimous_structural_membership(merged, ordered_members)
""",
        """            *detected_regions,
        ],
    )
@GLYPHS@    _inherit_unanimous_structural_membership(merged, ordered_members)
""",
        "ordered_members",
    ),
    _merged_glyphs(
        """        inline_math_regions=[region for member in members for region in member.inline_math_regions],
    )
    _inherit_unanimous_structural_membership(merged, members)
""",
        """        inline_math_regions=[region for member in members for region in member.inline_math_regions],
    )
@GLYPHS@    _inherit_unanimous_structural_membership(merged, members)
""",
        "members",
    ),
    _merged_glyphs(
        """        inline_math_regions=[region for member in ordered_members for region in member.inline_math_regions],
    )
    _inherit_unanimous_structural_membership(merged, ordered_members)
""",
        """        inline_math_regions=[region for member in ordered_members for region in member.inline_math_regions],
    )
@GLYPHS@    _inherit_unanimous_structural_membership(merged, ordered_members)
""",
        "ordered_members",
    ),
    # Tagged text keeps one box for the whole block as its public line, so paragraph
    # merging sees what it always saw. The measured lines go in ``_lines``, a private
    # field that DocVortex removes when the page is finished.
    Hook(
        _TAGGED,
        """            block["lines"] = [{"bbox": box}]
""",
        """            block["lines"] = [{"bbox": box}]
            measured: list[dict[str, object]] = []
            if node.actual_text is None:
                measured = [
                    char
                    for obj in members
                    for char in getattr(obj, "chars", ())
                    if isinstance(char, dict) and char.get("char_idx") not in self.suppressed_spaces
                ]
            glyph_lines = bioparser_glyphs.tagged_lines(measured, self.page.size)
            if glyph_lines:
                block["_lines"] = glyph_lines
""",
    ),
)

HOOKS = NATIVE_HOOKS + OCR_HOOKS + PAGES_HOOKS + NATIVE_LINE_HOOKS

FEATURE = Feature(
    name="glyph_edges",
    module="bioparser_glyphs",
    source=Path(__file__).resolve().parent,
    hooks=HOOKS,
    required={
        f"MinerU is not {MINERU_VERSION}": (_NATIVE, _CONTENT),
        f"DocVortex is not {DOCVORTEX_VERSION}": (
            _PAGES,
            _DOCUMENT,
            _PAGE_BLOCKS,
            _STRUCTURE,
            _MODELS,
            _PIPELINE,
            _MERGING,
            _TAGGED,
        ),
    },
)
