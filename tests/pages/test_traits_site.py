import json
from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import Any

from build_traits import build_site

from bioparser.traits.table import TraitTable, load_table, mammal_traits


def _later(version: str, mutate: Callable[[dict[str, Any]], object]) -> TraitTable:
    raw = json.loads(mammal_traits().model_dump_json())
    raw["version"] = version
    mutate(raw)
    return load_table(json.dumps(raw))


def test_the_home_page_and_the_trait_index_are_written(tmp_path: Path) -> None:
    build_site(tmp_path)

    assert (tmp_path / "index.html").is_file()
    assert mammal_traits().version in (tmp_path / "traits" / "index.html").read_text()
    assert (tmp_path / "traits" / "static" / "trait.js").is_file()


def test_every_trait_id_has_a_page_a_data_file_and_a_version_list_link(tmp_path: Path) -> None:
    build_site(tmp_path)
    table = mammal_traits()
    version = table.version
    version_page = (tmp_path / "traits" / version / "index.html").read_text()

    assert (tmp_path / "traits" / version / "traitlist.json").is_file()
    assert (tmp_path / "traits" / version / "full.json").is_file()
    for trait in table.traits:
        prefix, local = trait.trait_id.split(":", 1)
        page = (tmp_path / "traits" / prefix / local / "index.html").read_text()
        data = json.loads(
            (tmp_path / "traits" / version / "traits" / f"{prefix}-{local}.json").read_text()
        )
        assert escape(trait.definition) in page
        assert 'id="version"' in page
        assert data["trait"]["code"] == trait.code
        assert f'href="../{prefix}/{local}/?version={version}"' in version_page


def test_a_replaced_trait_links_to_its_replacement_and_lists_its_versions(tmp_path: Path) -> None:
    current = mammal_traits()

    def retire(raw: dict[str, Any]) -> None:
        raw["traits"][0]["status"] = "deprecated"
        raw["traits"][0]["replaced_by"] = raw["traits"][1]["code"]

    later = _later("2026.11.0", retire)
    build_site(tmp_path, {current.version: current, later.version: later})

    retired, replacement = current.traits[0], current.traits[1]
    prefix, local = retired.trait_id.split(":", 1)
    other_prefix, other_local = replacement.trait_id.split(":", 1)
    page = (tmp_path / "traits" / prefix / local / "index.html").read_text()

    assert f'href="../../{other_prefix}/{other_local}/?version=2026.11.0"' in page
    assert 'href="../../2026.11.0/"' in page
    assert replacement.code in page
    assert page.index('value="2026.11.0" selected') < page.index(f'value="{current.version}"')

    old = json.loads(
        (tmp_path / "traits" / current.version / "traits" / f"{prefix}-{local}.json").read_text()
    )
    new = json.loads(
        (tmp_path / "traits" / "2026.11.0" / "traits" / f"{prefix}-{local}.json").read_text()
    )
    assert old["replaced_by"] is None
    assert new["replaced_by"]["code"] == replacement.code
    assert new["replaced_by"]["since"] == "2026.11.0"


def test_a_trait_added_later_has_no_data_file_for_the_older_version(tmp_path: Path) -> None:
    current = mammal_traits()
    older = _later("2026.09.0", lambda raw: raw.update(traits=raw["traits"][:-1]))
    build_site(tmp_path, {"2026.09.0": older, current.version: current})

    prefix, local = current.traits[-1].trait_id.split(":", 1)
    assert not (tmp_path / "traits" / "2026.09.0" / "traits" / f"{prefix}-{local}.json").exists()
    page = (tmp_path / "traits" / prefix / local / "index.html").read_text()
    assert "2026.09.0" not in page


def test_text_from_the_list_is_escaped(tmp_path: Path) -> None:
    def hostile(raw: dict[str, Any]) -> None:
        raw["traits"][0]["definition"] = "<script>alert(1)</script>"

    table = _later(mammal_traits().version, hostile)
    build_site(tmp_path, {table.version: table})

    prefix, local = table.traits[0].trait_id.split(":", 1)
    page = (tmp_path / "traits" / prefix / local / "index.html").read_text()
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
