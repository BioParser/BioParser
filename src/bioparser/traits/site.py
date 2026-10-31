"""Static pages for the released trait lists.

One directory per released version, and one stable page per trait id. A version list links
to the trait as it was in that version. The trait page reads ``?version=`` and loads that
release. Run:

    uv run python -m bioparser.traits.site --out DIR
"""

import argparse
import json
from collections import defaultdict
from html import escape
from pathlib import Path

from bioparser.traits.table import (
    CategoricalTrait,
    CountTrait,
    NumericTrait,
    TraitTable,
    released_tables,
)
from bioparser.traits.traitlist import iri_for, traitlist_rows

_TRAIT_SCRIPT = """
<script>
const params = new URLSearchParams(location.search);
const select = document.getElementById("version");
const traitNode = document.getElementById("trait");
const traitId = traitNode.dataset.id;
const replacedIn = JSON.parse(traitNode.dataset.replaced || "{}");
const wanted = params.get("version");

function fill(trait, table, version) {
  document.querySelector("h1").textContent = trait.name;
  document.getElementById("trait-id").textContent = trait.trait_id;
  document.getElementById("definition").textContent = trait.definition;
  document.getElementById("kind").textContent = trait.kind;
  document.getElementById("status").textContent = trait.status;
  document.getElementById("domain").textContent = trait.domain;
  const unitLine = document.getElementById("unit-line");
  const levels = document.getElementById("levels");
  levels.replaceChildren();
  if (trait.levels) {
    unitLine.hidden = true;
    for (const level of trait.levels) {
      const item = document.createElement("li");
      item.textContent = level;
      levels.append(item);
    }
  } else {
    unitLine.hidden = false;
    document.getElementById("unit").textContent = trait.stored_unit;
  }
  const replacedLine = document.getElementById("replaced-line");
  const replacement = table.traits.find((entry) => entry.code === trait.replaced_by);
  const since = trait.replaced_by ? replacedIn[trait.replaced_by] : null;
  if (replacement) {
    const [prefix, local] = replacement.trait_id.split(":");
    const link = document.getElementById("replaced");
    link.href = `../../${prefix}/${local}/?version=${since || version}`;
    link.textContent = replacement.code;
    const when = document.getElementById("replaced-when");
    const replacedVersion = document.getElementById("replaced-in");
    if (since) {
      replacedVersion.href = `../../${since}/`;
      replacedVersion.textContent = since;
      when.hidden = false;
    } else {
      when.hidden = true;
    }
    replacedLine.hidden = false;
  } else {
    replacedLine.hidden = true;
  }
  const back = document.getElementById("version-list");
  back.href = `../../${version}/`;
  back.textContent = version;
}

async function load(version) {
  const response = await fetch(`../../${version}/full.json`);
  if (!response.ok) return;
  const table = await response.json();
  const trait = table.traits.find((entry) => entry.trait_id === traitId);
  if (!trait) return;
  fill(trait, table, version);
}

select.addEventListener("change", () => {
  const version = select.value;
  const url = new URL(location.href);
  url.searchParams.set("version", version);
  history.replaceState(null, "", url);
  load(version);
});

if (wanted && [...select.options].some((option) => option.value === wanted)) {
  select.value = wanted;
  load(wanted);
}
</script>
"""


def build_site(out: Path, releases: dict[str, TraitTable] | None = None) -> None:
    """Write ``traits/`` under ``out``. The default source is the packaged releases."""
    tables = released_tables() if releases is None else releases
    if not tables:
        raise ValueError("no released trait lists")
    root = out / "traits"
    root.mkdir(parents=True, exist_ok=True)
    versions = tuple(tables)
    latest = versions[-1]
    for version, table in tables.items():
        _write_version(root / version, table)
    _write_index(root, tables, latest)
    _write_traits(root, tables)
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
    by_domain: dict[str, list[NumericTrait | CountTrait | CategoricalTrait]] = defaultdict(list)
    for trait in table.traits:
        by_domain[trait.domain].append(trait)
    sections: list[str] = []
    for domain in table.domains:
        traits = by_domain.get(domain.name, [])
        if not traits:
            continue
        items = "\n".join(_version_item(trait, table.version) for trait in traits)
        sections.append(f"<h2>{escape(domain.name)}</h2>\n<ul>\n{items}\n</ul>")
    (directory / "index.html").write_text(
        _page(f"Trait list {table.version}", "\n".join(sections)),
        "utf-8",
    )


def _version_item(trait: NumericTrait | CountTrait | CategoricalTrait, version: str) -> str:
    prefix, local = trait.trait_id.split(":", 1)
    href = f"../{prefix}/{local}/?version={version}"
    label = f"{escape(trait.name)} <code>{escape(trait.code)}</code>"
    return f'<li><a href="{escape(href)}">{label}</a></li>'


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


def _write_traits(root: Path, releases: dict[str, TraitTable]) -> None:
    first: dict[str, str] = {}
    versions_for: dict[str, list[str]] = defaultdict(list)
    replaced_since: dict[str, dict[str, str]] = defaultdict(dict)
    latest_trait = {}
    for version, table in releases.items():
        for trait in table.traits:
            first.setdefault(trait.trait_id, version)
            versions_for[trait.trait_id].append(version)
            if trait.replaced_by and trait.replaced_by not in replaced_since[trait.trait_id]:
                replaced_since[trait.trait_id][trait.replaced_by] = version
            latest_trait[trait.trait_id] = (version, trait)
    by_code = {trait.code: trait for _, trait in latest_trait.values()}
    for trait_id, (version, trait) in latest_trait.items():
        prefix, local = trait_id.split(":", 1)
        directory = root / prefix / local
        directory.mkdir(parents=True, exist_ok=True)
        options = "\n".join(
            f'<option value="{escape(item)}"{" selected" if item == version else ""}>'
            f"{escape(item)}</option>"
            for item in reversed(versions_for[trait_id])
        )
        parts = [
            f'<p><label>Version <select id="version">\n{options}\n</select></label></p>',
            (
                f'<div id="trait" data-id="{escape(trait_id)}" '
                f'data-replaced="{escape(json.dumps(replaced_since[trait_id]))}">'
            ),
            '<p><code id="trait-id">' + escape(trait.trait_id) + "</code></p>",
            f'<p id="definition">{escape(trait.definition)}</p>',
            f'<p>Kind: <span id="kind">{escape(trait.kind)}</span></p>',
            f'<p>Status: <span id="status">{escape(trait.status)}</span></p>',
            f'<p>Domain: <span id="domain">{escape(trait.domain)}</span></p>',
            f'<p>First released in <span id="first">{escape(first[trait_id])}</span></p>',
        ]
        if isinstance(trait, CategoricalTrait):
            items = "\n".join(f"<li>{escape(level)}</li>" for level in trait.levels)
            parts.append('<p id="unit-line" hidden>Unit: <span id="unit"></span></p>')
            parts.append(f'<ul id="levels">\n{items}\n</ul>')
        else:
            parts.append(
                f'<p id="unit-line">Unit: <span id="unit">{escape(trait.stored_unit)}</span></p>'
            )
            parts.append('<ul id="levels"></ul>')
        replacement = by_code.get(trait.replaced_by) if trait.replaced_by else None
        since = replaced_since[trait_id].get(trait.replaced_by) if trait.replaced_by else None
        if replacement is None:
            parts.append(
                '<p id="replaced-line" hidden>Replaced by <a id="replaced"></a>'
                '<span id="replaced-when"> in <a id="replaced-in"></a></span></p>'
            )
        else:
            shown = since or version
            other_prefix, other_local = replacement.trait_id.split(":", 1)
            other_href = f"../../{other_prefix}/{other_local}/?version={shown}"
            when = ""
            if since:
                when = f' in <a id="replaced-in" href="../../{escape(since)}/">{escape(since)}</a>'
            parts.append(
                '<p id="replaced-line">Replaced by '
                f'<a id="replaced" href="{escape(other_href)}">{escape(replacement.code)}</a>'
                f'<span id="replaced-when">{when}</span></p>'
            )
        parts.append(
            f'<p><a id="version-list" href="../../{escape(version)}/">{escape(version)}</a></p>'
        )
        parts.append(f"<p>{escape(iri_for(releases[version], trait.trait_id))}</p>")
        parts.append("</div>")
        parts.append(_TRAIT_SCRIPT)
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
