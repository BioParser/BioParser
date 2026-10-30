"""Freeze the current trait list as a released baseline.

Run this when you cut a new list version, in the same change that bumps ``version``:

    uv run python tests/ets/freeze_traits.py

It adds the current version to ``released_traits.json``. It refuses to change a version
that is already frozen. A released version is final, so edit the list and bump the
version instead.
"""

import json
import sys
from pathlib import Path
from typing import Any

from bioparser.ets.traits import TraitTable, mammal_traits

BASELINE = Path(__file__).with_name("released_traits.json")


def snapshot(table: TraitTable) -> dict[str, dict[str, Any]]:
    """What a stored result depends on, per code. Names, definitions and status are free."""
    entries: dict[str, dict[str, Any]] = {}
    for trait in table.traits:
        entry: dict[str, Any] = {"kind": trait.kind, "trait_id": trait.trait_id}
        if trait.kind == "numeric":
            entry["stored_unit"] = trait.stored_unit
            entry["printed_units"] = dict(sorted(trait.printed_units.items()))
        elif trait.kind == "count":
            entry["stored_unit"] = trait.stored_unit
        else:
            entry["levels"] = list(trait.levels)
        entries[trait.code] = entry
    return entries


def load_baseline() -> dict[str, dict[str, dict[str, Any]]]:
    loaded: dict[str, dict[str, dict[str, Any]]] = json.loads(BASELINE.read_text("utf-8"))
    return loaded


def main() -> int:
    table = mammal_traits()
    baseline = load_baseline() if BASELINE.exists() else {}
    current = snapshot(table)
    frozen = baseline.get(table.version)
    if frozen is not None and frozen != current:
        print(
            f"version {table.version} is already frozen with different content. "
            "Bump the version in mammal_traits.json first.",
            file=sys.stderr,
        )
        return 1
    baseline[table.version] = current
    BASELINE.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", "utf-8")
    print(f"froze {table.version} with {len(current)} traits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
