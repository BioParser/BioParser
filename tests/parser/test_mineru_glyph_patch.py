"""The MinerU image patch only rewrites the 4.2.3 and DocVortex 0.6.24 text it was written against."""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "mineru_glyph_edges",
    Path(__file__).resolve().parents[2] / "docker" / "patches" / "mineru_glyph_edges.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_PATCH = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_PATCH)


def _write_tree(tmp_path: Path) -> tuple[Path, Path]:
    native = tmp_path / "mineru" / "backend" / "analysis" / "pdf" / "text" / "native.py"
    content = tmp_path / "mineru" / "backend" / "analysis" / "pdf" / "text" / "content.py"
    post = tmp_path / "docvortex" / "postprocess"
    native.parent.mkdir(parents=True)
    post.mkdir(parents=True)
    native.write_text(
        _PATCH._KEEP_OLD
        + _PATCH._DELETE_CONTENT_OLD
        + _PATCH._DELETE_PREBUILT_OLD
        + _PATCH._ASSIGN_OLD,
        encoding="utf-8",
    )
    content.write_text(_PATCH._LINE_ITEMS_OLD, encoding="utf-8")
    (post / "pages.py").write_text(
        _PATCH._PAGES_IMPORT_OLD
        + _PATCH._PAGES_STAMP_OLD
        + _PATCH._PAGES_START_OLD
        + "    if use_bbox:\n        merge_para_text_blocks(raw_pages)\n",
        encoding="utf-8",
    )
    (post / "page_blocks.py").write_text(_PATCH._PAGE_BLOCKS_NOTE_OLD, encoding="utf-8")
    (post / "pdf_structure.py").write_text(
        _PATCH._STRUCTURE_REMAP_OLD + _PATCH._STRUCTURE_NOTE_OLD,
        encoding="utf-8",
    )
    (post / "document.py").write_text(
        "from .pages import model_json_to_pages\n" + _PATCH._DV_DOCUMENT_OLD,
        encoding="utf-8",
    )
    pdf = tmp_path / "docvortex" / "analyzers" / "native" / "pdf"
    pdf.mkdir(parents=True)
    (pdf / "models.py").write_text(_PATCH._NATIVE_FIELD_OLD, encoding="utf-8")
    (pdf / "pipeline.py").write_text(
        "def _compact_prepared_lines(\n" + _PATCH._NATIVE_CLEAR_OLD + _PATCH._NATIVE_OUTPUT_OLD,
        encoding="utf-8",
    )
    (pdf / "line_merging.py").write_text(
        "".join(old for old, _new, _already in _PATCH._MERGE_COPIES),
        encoding="utf-8",
    )
    return native, content


def test_patch_refuses_a_different_mineru(tmp_path: Path) -> None:
    native, _content = _write_tree(tmp_path)
    native.write_text("def chars_to_content():\n    return None\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="does not match"):
        _PATCH.apply(tmp_path)


def test_patch_keeps_character_boxes_and_captures_lines(tmp_path: Path) -> None:
    native, content = _write_tree(tmp_path)
    _PATCH.apply(tmp_path)
    kept = native.read_text(encoding="utf-8")
    assert kept.count("_bioparser_keep_glyphs(span)") == 2
    assert kept.index("_bioparser_keep_glyphs(span)") < kept.index('del span.metadata["chars"]')
    assert "if native_contents is None else []" not in kept
    assert "_owned_span_assignments(_native_text, spans, median_span_height)" in kept
    built = content.read_text(encoding="utf-8")
    assert "bioparser_glyphs" in built
    assert "span_bbox" not in built
    pages = (tmp_path / "docvortex" / "postprocess" / "pages.py").read_text(encoding="utf-8")
    assert "_bioparser_note_flat" in pages
    assert "_bioparser_capture" not in pages
    assert pages.index("process_page_blocks(page_model_list, use_bbox=use_bbox)") < pages.index(
        "_bioparser_set_page_idx(page_idx)"
    )
    page_blocks = (tmp_path / "docvortex" / "postprocess" / "page_blocks.py").read_text(
        encoding="utf-8"
    )
    assert page_blocks.index("_normalize_raw_blocks") < page_blocks.index(
        "_bioparser_note_flat(blocks)"
    )
    assert page_blocks.index("_bioparser_note_flat(blocks)") < page_blocks.index(
        "_apply_caption_fallbacks"
    )
    structure = (tmp_path / "docvortex" / "postprocess" / "pdf_structure.py").read_text(
        encoding="utf-8"
    )
    assert structure.index("remap(block)") < structure.index(
        "_bioparser_remap_open_page(positions)"
    )
    assert "_bioparser_note_flat(trusted)" in structure
    document = (tmp_path / "docvortex" / "postprocess" / "document.py").read_text(encoding="utf-8")
    assert "bioparser_lines" in document
    assert "structure_extension" in document
    pipeline = (tmp_path / "docvortex" / "analyzers" / "native" / "pdf" / "pipeline.py").read_text(
        encoding="utf-8"
    )
    assert pipeline.index("_bioparser_freeze_glyphs(line, page_size)") < pipeline.index(
        "line.chars.clear()"
    )
    assert "_bioparser_lines(block, page_size)" in pipeline
    merging = (
        tmp_path / "docvortex" / "analyzers" / "native" / "pdf" / "line_merging.py"
    ).read_text(encoding="utf-8")
    assert merging.count("member.bioparser_glyphs") == 3
    _PATCH.apply(tmp_path)


def test_patch_refuses_docvortex_without_the_structure_argument(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    document = tmp_path / "docvortex" / "postprocess" / "document.py"
    document.write_text(
        "from .pages import model_json_to_pages\n"
        "def model_json_to_middle_json(model_json: ModelJson) -> MiddleJson:\n"
        '    """只执行确定性后处理，智能增强由调用方另行执行。"""\n'
        "    return MiddleJson(pages=model_json_to_pages(model_json))\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="does not match"):
        _PATCH.apply(tmp_path)
