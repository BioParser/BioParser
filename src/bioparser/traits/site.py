"""Static pages for the released trait lists.

One directory per released version, and one stable page per ``BPT`` code. A code never
changes its measurement, so the stable page can show the latest wording. Run:

    uv run python -m bioparser.traits.site --out DIR
"""

import argparse
import json
from html import escape
from pathlib import Path

from bioparser.traits.table import CategoricalTrait, TraitTable, released_tables
from bioparser.traits.traitlist import iri_for, traitlist_rows


def build_site(out: Path) -> None:
    """Write ``traits/`` under ``out`` from the packaged releases."""
    releases = released_tables()
    if not releases:
        raise ValueError("no released trait lists")
    root = out / "traits"
    root.mkdir(parents=True, exist_ok=True)
    versions = tuple(releases)
    latest = versions[-1]
    for version, table in releases.items():
        _write_version(root / version, table)
    _write_index(root, releases, latest)
    _write_codes(root / "BPT", releases)
    latest_page = root / "latest"
    latest_page.mkdir(exist_ok=True)
    (latest_page / "index.html").write_text(
        _page("Latest trait list", f'<p><a href="../{escape(latest)}/">{escape(latest)}</a></p>'),
        "utf-8",
    )


def _write_version(directory: Path, table: TraitTable) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rows = [row.model_dump(mode="json") for row in traitlist_rows(table, include_deprecated=True)]
    (directory / "traitlist.json").write_text(json.dumps(rows, indent=2) + "\n", "utf-8")
    (directory / "full.json").write_text(table.model_dump_json(indent=2) + "\n", "utf-8")
    items = "\n".join(
        f"<li>{escape(trait.name)} <code>{escape(trait.code)}</code></li>" for trait in table.traits
    )
    (directory / "index.html").write_text(
        _page(f"Trait list {table.version}", f"<ul>\n{items}\n</ul>"),
        "utf-8",
    )


def _write_index(root: Path, releases: dict[str, TraitTable], latest: str) -> None:
    table = releases[latest]
    versions = "\n".join(
        f'<li><a href="{escape(version)}/">{escape(version)}</a></li>' for version in releases
    )
    domains = "\n".join(
        f"<li>{escape(domain.name)}: {escape(domain.definition)}</li>" for domain in table.domains
    )
    body = (
        f'<p>Latest release: <a href="{escape(latest)}/">{escape(latest)}</a></p>'
        f"<h2>Versions</h2><ul>\n{versions}\n</ul>"
        f"<h2>Domains</h2><ul>\n{domains}\n</ul>"
    )
    (root / "index.html").write_text(_page("BioParser trait list", body), "utf-8")


def _write_codes(root: Path, releases: dict[str, TraitTable]) -> None:
    first: dict[str, str] = {}
    latest_trait = {}
    for version, table in releases.items():
        for trait in table.traits:
            if not trait.trait_id.startswith("BPT:"):
                continue
            local = trait.trait_id.split(":", 1)[1]
            first.setdefault(local, version)
            latest_trait[local] = (version, trait)
    for local, (version, trait) in latest_trait.items():
        directory = root / local
        directory.mkdir(parents=True, exist_ok=True)
        parts = [
            f"<p><code>{escape(trait.trait_id)}</code></p>",
            f"<p>{escape(trait.definition)}</p>",
            f"<p>Kind: {escape(trait.kind)}</p>",
            f"<p>Status: {escape(trait.status)}</p>",
            f"<p>First released in {escape(first[local])}</p>",
        ]
        if not isinstance(trait, CategoricalTrait):
            parts.append(f"<p>Unit: {escape(trait.stored_unit)}</p>")
        else:
            items = "\n".join(f"<li>{escape(level)}</li>" for level in trait.levels)
            parts.append(f"<ul>\n{items}\n</ul>")
        if trait.replaced_by:
            parts.append(f"<p>Replaced by {escape(trait.replaced_by)}</p>")
        parts.append(f"<p>{escape(iri_for(releases[version], trait.trait_id))}</p>")
        (directory / "index.html").write_text(_page(trait.name, "\n".join(parts)), "utf-8")


def _page(title: str, body: str) -> str:
    """An HTML page. ``body`` is already escaped by the caller. The title is escaped here."""
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{escape(title)}</title></head><body><h1>{escape(title)}</h1>\n{body}\n</body></html>\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Write the trait list pages")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    build_site(args.out)


if __name__ == "__main__":
    main()
