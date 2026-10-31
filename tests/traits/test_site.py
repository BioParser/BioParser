from html import escape

from pathlib import Path

from bioparser.traits.site import _page, build_site
from bioparser.traits.table import mammal_traits


def test_a_page_title_is_escaped() -> None:
    assert "&lt;" in _page("<script>", "<p>ok</p>")


def test_every_bpt_code_has_a_page_at_its_iri_path(tmp_path: Path) -> None:
    build_site(tmp_path)
    table = mammal_traits()
    version = table.version

    assert (tmp_path / "traits" / version / "traitlist.json").is_file()
    assert (tmp_path / "traits" / version / "full.json").is_file()
    assert version in (tmp_path / "traits" / version / "index.html").read_text()
    for trait in table.traits:
        if not trait.trait_id.startswith("BPT:"):
            continue
        local = trait.trait_id.split(":", 1)[1]
        page = (tmp_path / "traits" / "BPT" / local / "index.html").read_text()
        assert escape(trait.definition) in page
        assert local in page
