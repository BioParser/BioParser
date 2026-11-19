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
_PAGES = "docvortex/postprocess/pages.py"
_DOCUMENT = "docvortex/postprocess/document.py"
_PAGE_BLOCKS = "docvortex/postprocess/page_blocks.py"
_STRUCTURE = "docvortex/postprocess/pdf_structure.py"
_MODELS = "docvortex/analyzers/native/pdf/models.py"
_PIPELINE = "docvortex/analyzers/native/pdf/pipeline.py"
_MERGING = "docvortex/analyzers/native/pdf/line_merging.py"

# Each search string must occur in the published file. The patch module is not the source.
_ANCHORS: tuple[tuple[str, str], ...] = (
    (_NATIVE, _PATCH._KEEP_OLD),
    (_NATIVE, _PATCH._DELETE_CONTENT_OLD),
    (_NATIVE, _PATCH._DELETE_PREBUILT_OLD),
    (_NATIVE, _PATCH._ASSIGN_OLD),
    (_CONTENT, _PATCH._LINE_ITEMS_OLD),
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
    assert 'output_block["lines"] = _bioparser_lines(block, page_size)\n' in pipeline

    merging = _text(site, _MERGING)
    assert merging.count("merged.bioparser_glyphs") == 3


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
