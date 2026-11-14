"""Shared copies of the shipped trait list for the trait tests."""

import json
from importlib import resources
from typing import Any

from bioparser.traits.table import TraitTable, list_problems, load_table


def _table() -> dict[str, Any]:
    text = resources.files("bioparser.traits").joinpath("mammal_traits.json").read_text("utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def _load(table: dict[str, Any]) -> TraitTable:
    return load_table(json.dumps(table))


def _with_alias_data(**top: object) -> dict[str, Any]:
    data = _table()
    data.update(top)
    return data


def _problems(data: dict[str, Any]) -> list[Any]:
    return list_problems(json.dumps(data))
