"""The glyph patch rewrites the published MinerU and DocVortex sources.

The files come from the wheels named by ``MINERU_VERSION`` and ``DOCVORTEX_VERSION``.
"""

import io
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

import pytest

_PATCHES = Path(__file__).resolve().parents[3] / "docker" / "patches"
sys.path[:0] = [str(_PATCHES / "mineru"), str(_PATCHES)]

import glyph_edges.hooks as _PATCH
from lib.hooks import apply_feature

pytestmark = pytest.mark.network

_FEATURE = _PATCH.FEATURE


def _apply(site: Path) -> None:
    apply_feature(site, _FEATURE)


_RUNTIME = "bioparser_glyphs"


def _install(tmp_path: Path, sources: Path) -> Path:
    site = tmp_path / "site-packages"
    for package in ("mineru", "docvortex"):
        shutil.copytree(sources / package, site / package)
    return site


def _text(site: Path, relative: str) -> str:
    return (site / relative).read_text(encoding="utf-8")


def _files() -> list[str]:
    return list(dict.fromkeys(hook.path for hook in _FEATURE.hooks))


def _assert_rewritten(site: Path) -> None:
    assert (site / f"{_RUNTIME}.py").is_file()
    for relative in _files():
        text = _text(site, relative)
        compile(text, relative, "exec")
        # Every hook is in place, and a call into the runtime has its import.
        for hook in _FEATURE.hooks:
            if hook.path == relative:
                assert text.count(hook.new) == 1, hook.new
        if f"{_RUNTIME}." in text:
            assert text.count(f"import {_RUNTIME}\n") == 1, relative
    native = _text(site, _PATCH._NATIVE)
    assert native.count("bioparser_glyphs.keep_span_glyphs(span)") == 2
    first_delete = native.index('del span.metadata["chars"]')
    second_delete = native.index('del span.metadata["chars"]', first_delete + 1)
    assert native.index("keep_span_glyphs(span)") < first_delete
    assert native.index("keep_span_glyphs(span)", first_delete) < second_delete
    assert "if native_contents is None else []" not in native
    assert _text(site, _PATCH._MERGING).count("merged.bioparser_glyphs") == 3
    content = _text(site, _PATCH._CONTENT)
    assert "BlockType.PAGE_FOOTNOTE}" in content
    assert "not has_nonempty_content and block_type != BlockType.PAGE_FOOTNOTE" in content


# Each search string must occur in the published file. The patch module is not the source.
_ANCHORS: tuple[tuple[str, str], ...] = tuple((hook.path, hook.old) for hook in _FEATURE.hooks)


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
    _apply(site)
    _assert_rewritten(site)
    snapshot = {relative: _text(site, relative) for relative, _anchor in _ANCHORS}
    _apply(site)
    assert snapshot == {relative: _text(site, relative) for relative in snapshot}


def test_patch_refuses_moved_mineru_text(tmp_path: Path, published_sources: Path) -> None:
    site = _install(tmp_path, published_sources)
    native = site / _PATCH._NATIVE
    text = native.read_text(encoding="utf-8")
    needle = 'del span.metadata["chars"]\n'
    assert needle in text
    native.write_text(text.replace(needle, 'del span.metadata["chars_moved"]\n'), encoding="utf-8")
    with pytest.raises(SystemExit, match="does not match"):
        _apply(site)


def test_patch_refuses_a_moved_docvortex_hook(tmp_path: Path, published_sources: Path) -> None:
    site = _install(tmp_path, published_sources)
    structure = site / _PATCH._STRUCTURE
    text = structure.read_text(encoding="utf-8")
    needle = "    for block in output:\n        remap(block)\n"
    assert text.count(needle) == 1
    structure.write_text(
        text.replace(needle, "    for block in output:\n        remap_block(block)\n"),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="does not match"):
        _apply(site)


def test_patch_refuses_docvortex_without_the_structure_argument(
    tmp_path: Path, published_sources: Path
) -> None:
    site = _install(tmp_path, published_sources)
    document = site / _PATCH._DOCUMENT
    text = document.read_text(encoding="utf-8")
    needle = 'structure_extension=extensions.get("docvortex_pdf_structure")'
    assert text.count(needle) == 1
    document.write_text(
        text.replace(needle, "pages=model_json_to_pages(model_json)", 1), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="does not match"):
        _apply(site)
