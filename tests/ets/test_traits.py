import json
from importlib import resources
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from bioparser.ets.traits import (
    CategoricalTrait,
    CountTrait,
    NumericTrait,
    TraitTable,
    json_schema,
    load_table,
    mammal_traits,
)

# The first release. A later release may add traits and may deprecate one. It may not remove a
# code or change what a code means. Update this snapshot in the same change as such an edit.
_FIRST_RELEASE_NUMERIC_UNITS = {
    "body_mass": ("g", {"g", "kg"}),
    "body_length": ("mm", {"mm", "cm", "m"}),
    "tail_length": ("mm", {"mm", "cm", "m"}),
}


def _table() -> dict[str, Any]:
    text = resources.files("bioparser.ets").joinpath("mammal_traits.json").read_text("utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def _load(table: dict[str, Any]) -> TraitTable:
    return load_table(json.dumps(table))


def test_the_shipped_list_loads_and_has_every_value_kind() -> None:
    table = mammal_traits()

    kinds = {type(trait) for trait in table.traits}
    assert kinds == {NumericTrait, CountTrait, CategoricalTrait}
    assert len(table.traits) >= 60


def test_the_extractor_codes_are_on_the_list_with_the_same_units() -> None:
    table = mammal_traits()

    for code, (stored, printed) in _FIRST_RELEASE_NUMERIC_UNITS.items():
        trait = table.get(code)
        assert isinstance(trait, NumericTrait)
        assert trait.stored_unit == stored
        assert printed <= set(trait.printed_units)


def test_the_extractor_closed_set_is_covered() -> None:
    from bioparser.extract.backend.default.schema import Trait

    for code in get_args(Trait):
        assert mammal_traits().get(code) is not None


def test_known_vt_terms_are_used_and_every_prefix_is_declared() -> None:
    table = mammal_traits()
    body_mass = table.get("body_mass")

    assert body_mass is not None
    assert body_mass.trait_id == "VT:0001259"
    for trait in table.traits:
        assert trait.trait_id.split(":")[0] in table.id_prefixes


def test_head_body_and_total_length_are_separate_traits() -> None:
    table = mammal_traits()
    head_body = table.get("head_body_length")
    total = table.get("total_length")

    assert head_body is not None and total is not None
    assert head_body.trait_id != total.trait_id


def test_select_expands_domains_and_codes_and_rejects_unknown_names() -> None:
    table = mammal_traits()

    chosen = table.select(domains=("acoustics",), codes=("body_mass",))
    assert "call_duration" in chosen and "body_mass" in chosen
    with pytest.raises(ValueError, match="unknown"):
        table.select(domains=("diet",))
    with pytest.raises(ValueError, match="unknown"):
        table.select(codes=("wing_length",))


def test_a_deprecated_trait_is_left_out_of_a_domain_selection_but_still_resolves() -> None:
    data = _table()
    data["traits"][0]["status"] = "deprecated"
    table = _load(data)
    code = data["traits"][0]["code"]
    domain = data["traits"][0]["domain"]

    assert table.get(code) is not None
    assert code not in table.select(domains=(domain,))
    assert code in table.select(codes=(code,))


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda d: d["traits"][1].update(code=d["traits"][0]["code"]), "codes are not unique"),
        (
            lambda d: d["traits"][1].update(trait_id=d["traits"][0]["trait_id"]),
            "ids are not unique",
        ),
        (lambda d: d["traits"][0].update(domain="nowhere"), "unknown domain"),
        (lambda d: d["traits"][0].update(trait_id="XX:0000001"), "prefix"),
        (lambda d: d["traits"][0].update(trait_id="BPT:12"), "trait_id"),
        (lambda d: d["traits"][0].update(code="Body Mass"), "code"),
        (lambda d: d["traits"][0]["printed_units"].update(kg="0"), "positive"),
        (lambda d: d["traits"][0]["printed_units"].update(kg="heavy"), "decimal"),
        (lambda d: d["traits"][0].update(stored_unit="lb"), "stored_unit"),
        (lambda d: d["traits"][0].update(replaced_by="body_length"), "deprecated"),
        (lambda d: d.update(version="1"), "version"),
    ],
)
def test_a_bad_entry_is_rejected(edit: Any, message: str) -> None:
    data = _table()
    edit(data)

    with pytest.raises(ValidationError, match=message):
        _load(data)


def test_an_id_prefix_may_have_no_iri_yet() -> None:
    table = mammal_traits()

    assert table.id_prefixes["BPT"] is None
    assert table.id_prefixes["VT"] is not None


def test_a_count_can_name_its_own_unit() -> None:
    teats = mammal_traits().get("teat_number")

    assert isinstance(teats, CountTrait)
    assert teats.stored_unit == "teats"


def test_approximate_units_must_be_printed_units() -> None:
    data = _table()
    data["traits"][0]["approximate_units"] = ["fortnight"]

    with pytest.raises(ValidationError, match="approximate_units"):
        _load(data)


def test_a_categorical_trait_needs_distinct_levels() -> None:
    data = _table()
    categorical = next(trait for trait in data["traits"] if trait["kind"] == "categorical")
    categorical["levels"] = ["yes", "Yes"]

    with pytest.raises(ValidationError, match="distinct"):
        _load(data)


def test_a_replacement_must_name_a_code_on_the_list() -> None:
    data = _table()
    data["traits"][0].update(status="deprecated", replaced_by="nothing_here")

    with pytest.raises(ValidationError, match="unknown code"):
        _load(data)


def test_the_json_schema_describes_every_kind() -> None:
    schema = json.dumps(json_schema())

    for name in ("NumericTrait", "CountTrait", "CategoricalTrait"):
        assert name in schema
