"""Freeze the current trait list as a released version.

Run this when you cut a new list version, in the same change that bumps ``version``:

    uv run python -m bioparser.traits.release

It copies ``mammal_traits.json`` to ``releases/<version>.json``. It refuses to change a
version that already exists, and it refuses a list that breaks an earlier release.
"""

import sys
from importlib import resources
from pathlib import Path

from bioparser.traits.table import TraitTable, load_table, mammal_traits, released_tables

RELEASES = Path(str(resources.files("bioparser.traits"))) / "releases"


def compatibility_problems(old: TraitTable, new: TraitTable) -> list[str]:
    """Why a later list breaks an earlier release. Names and definitions may be reworded.

    The identifier is not checked. It may move to a public ontology term, and that is a
    release with a new version.
    """
    problems: list[str] = []
    for before in old.traits:
        after = new.get(before.code)
        if after is None:
            problems.append(f"{before.code} was removed, deprecate it instead")
            continue
        if after.kind != before.kind:
            problems.append(f"{before.code} changed kind")
            continue
        if before.status == "deprecated" and after.status == "active":
            problems.append(f"{before.code} was deprecated and is active again")
        if before.kind == "numeric" and after.kind == "numeric":
            if after.stored_unit != before.stored_unit:
                problems.append(f"{before.code} changed its stored unit")
            for unit, factor in before.printed_units.items():
                if after.printed_units.get(unit) != factor:
                    problems.append(f"{before.code} changed the factor for {unit}")
            if not set(before.approximate_units) <= set(after.approximate_units):
                problems.append(f"{before.code} dropped an approximate unit")
        elif before.kind == "count" and after.kind == "count":
            if after.stored_unit != before.stored_unit:
                problems.append(f"{before.code} changed its stored unit")
        elif before.kind == "categorical" and after.kind == "categorical":
            if not set(before.levels) <= set(after.levels):
                problems.append(f"{before.code} lost a level")
    return problems


def main() -> int:
    table = mammal_traits()
    target = RELEASES / f"{table.version}.json"
    source = RELEASES.parent / "mammal_traits.json"
    if target.exists():
        if load_table(target.read_text("utf-8")) != table:
            print(
                f"version {table.version} is already released with different content. "
                "Bump the version in mammal_traits.json first.",
                file=sys.stderr,
            )
            return 1
        print(f"{table.version} is already released")
        return 0
    problems = [
        f"{version}: {problem}"
        for version, earlier in released_tables().items()
        for problem in compatibility_problems(earlier, table)
    ]
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    target.write_text(source.read_text("utf-8"), "utf-8")
    print(f"released {table.version} with {len(table.traits)} traits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
