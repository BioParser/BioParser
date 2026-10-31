import json
from html import escape
from pathlib import Path

from bioparser.traits.site import _page, build_site
from bioparser.traits.table import load_table, mammal_traits


def test_a_page_title_is_escaped() -> None:
    assert "&lt;" in _page("<script>", "<p>ok</p>")


def test_every_trait_id_has_a_page_and_the_version_list_links_to_it(tmp_path: Path) -> None:
    build_site(tmp_path)
    table = mammal_traits()
    version = table.version
    version_page = (tmp_path / "traits" / version / "index.html").read_text()

    assert (tmp_path / "traits" / version / "traitlist.json").is_file()
    assert (tmp_path / "traits" / version / "full.json").is_file()
    for trait in table.traits:
        prefix, local = trait.trait_id.split(":", 1)
        page = (tmp_path / "traits" / prefix / local / "index.html").read_text()
        assert escape(trait.definition) in page
        assert f"?version={version}" in version_page
        assert f"../{prefix}/{local}/?version={version}" in version_page
        assert 'id="version"' in page
        assert "URLSearchParams" in page


def test_a_replaced_trait_links_to_its_replacement_and_lists_its_versions(tmp_path: Path) -> None:
    current = mammal_traits()
    raw = json.loads(current.model_dump_json())
    raw["version"] = "2026.11.0"
    raw["traits"][0]["status"] = "deprecated"
    raw["traits"][0]["replaced_by"] = raw["traits"][1]["code"]
    later = load_table(json.dumps(raw))
    build_site(tmp_path, {current.version: current, later.version: later})

    retired = raw["traits"][0]
    prefix, local = retired["trait_id"].split(":", 1)
    page = (tmp_path / "traits" / prefix / local / "index.html").read_text()
    replacement = raw["traits"][1]
    other_prefix, other_local = replacement["trait_id"].split(":", 1)

    assert f'href="../../{other_prefix}/{other_local}/?version=2026.11.0"' in page
    assert 'in <a id="replaced-in" href="../../2026.11.0/">2026.11.0</a>' in page
    assert replacement["code"] in page
    assert page.index('value="2026.11.0" selected') < page.index(f'value="{current.version}"')
