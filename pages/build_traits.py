"""Build the BioParser site for GitHub Pages.

Writes the home page and the published trait lists: one directory per version, one stable page
per trait id, and one small JSON file per trait and version. The trait page reads
``?version=`` and fetches that file. The markup is in ``templates/``, and the CSS and script
are in ``static/``. Run:

    uv run python pages/build_traits.py --out DIR
"""

import argparse
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from bioparser.traits.table import CategoricalTrait, Trait, TraitTable, load_table
from bioparser.traits.traitlist import iri_for, traitlist_rows

HERE = Path(__file__).parent
_RELEASES = HERE.parent / "data" / "traits" / "releases"
_ENV = Environment(
    loader=FileSystemLoader(HERE / "templates"),
    autoescape=True,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def build_site(out: Path, releases: dict[str, TraitTable] | None = None) -> None:
    """Write the home page and ``traits/`` under ``out``. The default is the published copies."""
    tables = _published() if releases is None else _ordered(releases)
    if not tables:
        raise ValueError("no published trait lists")
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / "home" / "index.html", out / "index.html")
    root = out / "traits"
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(HERE / "static", root / "static", dirs_exist_ok=True)
    latest = next(reversed(tables))
    history = _History(tables)
    versions = list(reversed(tables))
    for table in tables.values():
        _write_version(root / table.version, table, versions, latest)
    _write_version(root, tables[latest], versions, latest, index=True)
    _write(root / "latest" / "index.html", "latest.html", root_path="../", latest=latest)
    for trait_id in history.versions_for:
        _write_trait(root, tables, history, trait_id, latest)


class _History:
    """Which published versions contain each trait id."""

    def __init__(self, tables: dict[str, TraitTable]) -> None:
        self.versions_for: dict[str, list[str]] = defaultdict(list)
        for version, table in tables.items():
            for trait in table.traits:
                self.versions_for[trait.trait_id].append(version)

    def first(self, trait_id: str) -> str:
        return self.versions_for[trait_id][0]

    def latest(self, trait_id: str) -> str:
        return self.versions_for[trait_id][-1]


def _published() -> dict[str, TraitTable]:
    tables = [
        load_table(path.read_text("utf-8"))
        for path in _RELEASES.iterdir()
        if path.name.endswith(".json")
    ]
    return _ordered({table.version: table for table in tables})


def _ordered(tables: dict[str, TraitTable]) -> dict[str, TraitTable]:
    return {version: tables[version] for version in sorted(tables, key=_version_key)}


def _version_key(version: str) -> tuple[int, int, int]:
    year, month, revision = version.split(".")
    return (int(year), int(month), int(revision))


def _write(path: Path, template: str, **context: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_ENV.get_template(template).render(**context), "utf-8")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", "utf-8")


def _split(trait_id: str) -> tuple[str, str]:
    prefix, local = trait_id.split(":", 1)
    return prefix, local


def _write_version(
    directory: Path, table: TraitTable, versions: list[str], latest: str, index: bool = False
) -> None:
    """A version list. The trait list index is the latest version's list at the top address."""
    root_path = "" if index else "../"
    if not index:
        _write_data(directory, table)
    sections = []
    for domain in table.domains:
        traits = [trait for trait in table.traits if trait.domain == domain.name]
        if traits:
            sections.append(
                {
                    "domain": domain,
                    "traits": [
                        {"trait": trait, "href": _href(trait, root_path), "unit": _unit(trait)}
                        for trait in traits
                    ],
                }
            )
    _write(
        directory / "index.html",
        "version.html",
        root_path=root_path,
        here=f"{table.version}/" if index else "",
        is_index=index,
        table=table,
        latest=latest,
        versions=versions,
        sections=sections,
        count=len(table.traits),
    )


def _write_data(directory: Path, table: TraitTable) -> None:
    rows = [row.model_dump(mode="json") for row in traitlist_rows(table)]
    _write_json(directory / "traitlist.json", rows)
    (directory / "full.json").write_text(table.model_dump_json(indent=2) + "\n", "utf-8")


def _unit(trait: Trait) -> str:
    """What to show in a list row: the trait unit, or the number of levels."""
    if isinstance(trait, CategoricalTrait):
        return f"{len(trait.levels)} levels"
    return trait.trait_unit


def _href(trait: Trait, root_path: str) -> str:
    prefix, local = _split(trait.trait_id)
    return f"{root_path}{prefix}/{local}/"


def _write_trait(
    root: Path, tables: dict[str, TraitTable], history: _History, trait_id: str, latest: str
) -> None:
    prefix, local = _split(trait_id)
    for version in history.versions_for[trait_id]:
        table = tables[version]
        trait = next(entry for entry in table.traits if entry.trait_id == trait_id)
        _write_json(
            root / version / "traits" / f"{prefix}-{local}.json",
            _payload(table, trait),
        )
    version = history.latest(trait_id)
    table = tables[version]
    trait = next(entry for entry in table.traits if entry.trait_id == trait_id)
    _write(
        root / prefix / local / "index.html",
        "trait.html",
        root_path="../../",
        trait=trait,
        version=version,
        versions=list(reversed(history.versions_for[trait_id])),
        latest=latest,
        first=history.first(trait_id),
        iri=iri_for(table, trait.trait_id),
        domain=next(domain for domain in table.domains if domain.name == trait.domain),
    )


def _payload(table: TraitTable, trait: Trait) -> dict[str, Any]:
    """What the trait page needs to show one trait as it was in one version."""
    domain = next(item for item in table.domains if item.name == trait.domain)
    return {
        "version": table.version,
        "trait": trait.model_dump(mode="json"),
        "iri": iri_for(table, trait.trait_id),
        "domain_definition": domain.definition,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Write the BioParser site")
    parser.add_argument("--out", type=Path, required=True)
    build_site(parser.parse_args().out)


if __name__ == "__main__":
    main()
