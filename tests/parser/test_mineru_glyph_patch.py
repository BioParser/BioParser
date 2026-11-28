"""The glyph patch rewrites the published MinerU and DocVortex sources.

The files come from the wheels named by ``MINERU_VERSION`` and ``DOCVORTEX_VERSION``.
"""

import importlib.util
import io
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.network

_SPEC = importlib.util.spec_from_file_location(
    "mineru_glyph_edges",
    Path(__file__).resolve().parents[2] / "docker" / "patches" / "mineru_glyph_edges.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_PATCH = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_PATCH)

_NATIVE = "mineru/backend/analysis/pdf/text/native.py"
_CONTENT = "mineru/backend/analysis/pdf/text/content.py"
_OCR = "mineru/backend/analysis/pdf/ocr.py"
_RECOGNIZER = "mineru/model/_internal/pytorchocr/infer/predict_rec.py"
_DECODE = "mineru/model/_internal/pytorchocr/postprocess/rec_postprocess.py"
_PADDLE = "mineru/model/ocr/pytorch_paddle.py"
_ONNX = "mineru/model/ocr/pp_ocr_v6_onnx.py"
_FORMULAS = "mineru/backend/analysis/pdf/formulas.py"
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

# Each search string must occur in the published file. The patch module is not the source.
_ANCHORS: tuple[tuple[str, str], ...] = (
    (_NATIVE, _PATCH._KEEP_OLD),
    (_NATIVE, _PATCH._DELETE_CONTENT_OLD),
    (_NATIVE, _PATCH._DELETE_PREBUILT_OLD),
    (_NATIVE, _PATCH._ASSIGN_OLD),
    (_CONTENT, _PATCH._LINE_ITEMS_OLD),
    (_CONTENT, _PATCH._LINE_KEEP_OLD),
    (_CONTENT, _PATCH._TARGET_IMPORT_OLD),
    (_CONTENT, _PATCH._TARGET_TYPES_OLD),
    (_CONTENT, _PATCH._OCR_SPAN_OLD),
    (_OCR, _PATCH._OCR_IMPORT_OLD),
    (_OCR, _PATCH._OCR_APPLY_OLD),
    (_RECOGNIZER, _PATCH._REC_CALL_OLD),
    (_RECOGNIZER, _PATCH._REC_POST_OLD),
    (_RECOGNIZER, _PATCH._REC_NAN_OLD),
    (_DECODE, _PATCH._DECODE_OLD),
    (_PADDLE, _PATCH._PADDLE_REC_OLD),
    (_ONNX, _PATCH._ONNX_DECODE_OLD),
    (_ONNX, _PATCH._ONNX_CALL_OLD),
    (_ONNX, _PATCH._ONNX_ASSIGN_OLD),
    (_ONNX, _PATCH._ONNX_REC_OLD),
    (_CONTENT, _PATCH._POST_OCR_OLD),
    (_FORMULAS, _PATCH._FORMULA_OCR_OLD),
    (_UNET, _PATCH._UNET_OCR_OLD),
    (_TABLE_ORI, _PATCH._TABLE_ORI_OLD),
    (_PAGES, _PATCH._PAGES_IMPORT_OLD),
    (_PAGES, _PATCH._PAGES_START_OLD),
    (_PAGES, _PATCH._PAGES_STAMP_OLD),
    (_PAGE_BLOCKS, _PATCH._PAGE_BLOCKS_NOTE_OLD),
    (_STRUCTURE, _PATCH._STRUCTURE_REMAP_OLD),
    (_STRUCTURE, _PATCH._STRUCTURE_NOTE_OLD),
    (_DOCUMENT, _PATCH._DOC_IMPORT_OLD),
    (_DOCUMENT, _PATCH._DV_DOCUMENT_OLD),
    (_MODELS, _PATCH._NATIVE_FIELD_OLD),
    (_PIPELINE, _PATCH._NATIVE_CLEAR_OLD),
    (_PIPELINE, _PATCH._NATIVE_OUTPUT_OLD),
    *((_MERGING, old) for old, _new, _already in _PATCH._MERGE_COPIES),
    (_TAGGED, _PATCH._TAGGED_CLASS_OLD),
    (_TAGGED, _PATCH._TAGGED_KIND_OLD),
    (_TAGGED, _PATCH._TAGGED_CALL_OLD),
)


def _install(tmp_path: Path, sources: Path) -> Path:
    site = tmp_path / "site-packages"
    for package in ("mineru", "docvortex"):
        shutil.copytree(sources / package, site / package)
    return site


def _text(site: Path, relative: str) -> str:
    return (site / relative).read_text(encoding="utf-8")


def _assert_rewritten(site: Path) -> None:
    native = _text(site, _NATIVE)
    assert native.count("def _bioparser_keep_glyphs") == 1
    assert native.count("_bioparser_keep_glyphs(span)") == 2
    first_delete = native.index('del span.metadata["chars"]')
    second_delete = native.index('del span.metadata["chars"]', first_delete + 1)
    assert native.index("_bioparser_keep_glyphs(span)") < first_delete
    assert native.index("_bioparser_keep_glyphs(span)", first_delete) < second_delete
    assert "if native_contents is None else []" not in native

    content = _text(site, _CONTENT)
    assert 'span.metadata.get("bioparser_glyphs")' in content
    assert "ContentType.INLINE_EQUATION" in content
    assert 'glyph["latex"] = latex' in content
    assert 'ocr_res.get("bioparser_chars")' in content
    assert 'glyphs.append({"t": text, "x0": box[0], "x1": box[2]})' not in content
    assert _PATCH._LINE_KEEP_NEW in content
    assert "set(NATURAL_LANGUAGE_CONTENT_BLOCK_TYPES)" in content
    assert (
        'if block_type in LINE_METADATA_BLOCK_TYPES:\n            block_item["lines"] = _build_ocr_det_line_items'
        not in content
    )

    ocr = _text(site, _OCR)
    assert "def _bioparser_ocr_chars" in ocr
    assert 'need_ocr_res["bioparser_chars"]' in ocr
    recognizer = _text(site, _RECOGNIZER)
    assert "return_word_box=False" in recognizer
    assert "wh_ratio_list=wh_ratio_list" in recognizer
    assert "result_list.append((text, mean_conf, [column_count, centers]))" in _text(site, _DECODE)
    assert "return_word_box=True" in _text(site, _PADDLE)
    onnx = _text(site, _ONNX)
    assert "column_count = int(len(idx))" in onnx
    assert "column_count * scale" in onnx
    assert onnx.count("return_word_box=True") == 1
    assert "ocr_text, ocr_score = ocr_res[0], ocr_res[1]" in content
    assert 'span.metadata["bioparser_glyphs"] = chars' in content
    assert "if not glyphs:" in native
    assert "ocr_text = ocr_result[0]" in _text(site, _FORMULAS)
    assert "ocr_text, ocr_score = ocr_res[0], ocr_res[1]" in _text(site, _UNET)
    assert "text, score = rec_item[0], rec_item[1]" in _text(site, _TABLE_ORI)

    pages = _text(site, _PAGES)
    assert pages.count("def _bioparser_note_flat") == 1
    assert (
        "    page_blocks = process_page_blocks(page_model_list, use_bbox=use_bbox)\n"
        "    _bioparser_set_page_idx(page_idx)\n"
    ) in pages

    page_blocks = _text(site, _PAGE_BLOCKS)
    assert (
        "    blocks = _normalize_raw_blocks(page_model_list)\n"
        "    from .pages import _bioparser_note_flat\n"
        "\n"
        "    _bioparser_note_flat(blocks)\n"
        "    _apply_caption_fallbacks(blocks, use_bbox=resolved_use_bbox)\n"
    ) in page_blocks

    structure = _text(site, _STRUCTURE)
    assert (
        "        remap(block)\n"
        "    from .pages import _bioparser_note_flat, _bioparser_remap_open_page\n"
        "\n"
        "    _bioparser_remap_open_page(positions)\n"
    ) in structure
    assert "_bioparser_note_flat(trusted)\n    return output\n" in structure

    document = _text(site, _DOCUMENT)
    assert document.count(_PATCH._DOC_IMPORT_NEW) == 1
    assert document.index(_PATCH._DOC_IMPORT_NEW) < document.index("def model_json_to_middle_json")
    assert (
        'middle_json.extensions = {**middle_json.extensions, "bioparser_lines": harvested}'
        in document
    )

    pipeline = _text(site, _PIPELINE)
    assert (
        "        _bioparser_freeze_glyphs(line, page_size)\n        line.chars.clear()\n"
    ) in pipeline
    assert "if lines or normalized_type in _LINE_METADATA_OUTPUT_TYPES:" in pipeline
    assert 'output_block["lines"] = lines\n' in pipeline

    merging = _text(site, _MERGING)
    assert merging.count("merged.bioparser_glyphs") == 3

    tagged = _text(site, _TAGGED)
    assert tagged.count("def _bioparser_tagged_lines") == 1
    assert "_bioparser_tagged_lines(measured, self.page.size)" in tagged
    assert _PATCH._TAGGED_KIND_NEW in tagged


def _wheel_bytes(project: str, version: str) -> bytes:
    """The wheel for one pin. A pure Python wheel is used when the project publishes one."""
    with urllib.request.urlopen(
        f"https://pypi.org/pypi/{project}/{version}/json", timeout=60
    ) as response:
        payload = json.load(response)
    wheels = [item for item in payload["urls"] if item["packagetype"] == "bdist_wheel"]
    pure = [item for item in wheels if item["filename"].endswith("py3-none-any.whl")]
    chosen = (pure or wheels)[0]
    with urllib.request.urlopen(chosen["url"], timeout=60) as response:
        downloaded = response.read()
    if not isinstance(downloaded, bytes):
        raise TypeError(project)
    return downloaded


@pytest.fixture(scope="module")
def published_sources(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The published files the patch rewrites, taken from the pinned wheels."""
    from bioparser.parser.backend.mineru.schema import DOCVORTEX_VERSION, MINERU_VERSION

    root = tmp_path_factory.mktemp("published-sources")
    wheels = {
        "mineru": _wheel_bytes("mineru", MINERU_VERSION),
        "docvortex": _wheel_bytes("docvortex", DOCVORTEX_VERSION),
    }
    for relative in dict.fromkeys(path for path, _anchor in _ANCHORS):
        project = relative.split("/", 1)[0]
        with zipfile.ZipFile(io.BytesIO(wheels[project])) as archive:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(relative))
    return root


def test_published_sources_contain_every_anchor(published_sources: Path) -> None:
    for relative, anchor in _ANCHORS:
        assert anchor in (published_sources / relative).read_text(encoding="utf-8")
    for path in published_sources.rglob("*.py"):
        assert "_bioparser_" not in path.read_text(encoding="utf-8")


def test_patch_rewrites_published_sources_once(tmp_path: Path, published_sources: Path) -> None:
    site = _install(tmp_path, published_sources)
    _PATCH.apply(site)
    _assert_rewritten(site)
    snapshot = {relative: _text(site, relative) for relative, _anchor in _ANCHORS}
    _PATCH.apply(site)
    assert snapshot == {relative: _text(site, relative) for relative in snapshot}


def test_patch_refuses_moved_mineru_text(tmp_path: Path, published_sources: Path) -> None:
    site = _install(tmp_path, published_sources)
    native = site / _NATIVE
    text = native.read_text(encoding="utf-8")
    needle = "def chars_to_content(\n"
    assert text.count(needle) == 1
    native.write_text(text.replace(needle, "def chars_to_content_moved(\n", 1), encoding="utf-8")
    with pytest.raises(SystemExit, match="does not match"):
        _PATCH.apply(site)


def test_patch_refuses_a_moved_docvortex_import(tmp_path: Path, published_sources: Path) -> None:
    site = _install(tmp_path, published_sources)
    document = site / _DOCUMENT
    text = document.read_text(encoding="utf-8")
    assert text.count(_PATCH._DOC_IMPORT_OLD) == 1
    document.write_text(
        text.replace(
            _PATCH._DOC_IMPORT_OLD,
            "from .pages import model_json_to_pages as build_pages\n",
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="does not match"):
        _PATCH.apply(site)


def test_patch_refuses_docvortex_without_the_structure_argument(
    tmp_path: Path, published_sources: Path
) -> None:
    site = _install(tmp_path, published_sources)
    document = site / _DOCUMENT
    text = document.read_text(encoding="utf-8")
    needle = 'structure_extension=extensions.get("docvortex_pdf_structure")'
    assert text.count(needle) == 1
    document.write_text(
        text.replace(needle, "pages=model_json_to_pages(model_json)", 1), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="does not match"):
        _PATCH.apply(site)
