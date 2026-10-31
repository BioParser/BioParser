import json
from importlib import resources
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from bioparser.traits.release import compatibility_problems
from bioparser.traits.table import (
    CategoricalTrait,
    CountTrait,
    NumericTrait,
    TraitTable,
    json_schema,
    load_table,
    mammal_traits,
    released_tables,
    version_sort_key,
)


def _table() -> dict[str, Any]:
    text = resources.files("bioparser.traits").joinpath("mammal_traits.json").read_text("utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def _load(table: dict[str, Any]) -> TraitTable:
    return load_table(json.dumps(table))


def test_the_shipped_list_loads_and_has_every_value_kind() -> None:
    table = mammal_traits()

    kinds = {type(trait) for trait in table.traits}
    assert kinds == {NumericTrait, CountTrait, CategoricalTrait}
    assert len(table.traits) >= 60


def test_the_current_version_is_released_and_unchanged() -> None:
    # Any edit to the list needs a new version. Bump `version`, then run
    # `uv run python -m bioparser.traits.release` in the same change.
    table = mammal_traits()
    released = released_tables()

    assert table.version in released, "release this version with bioparser.traits.release"
    assert released[table.version] == table, "bump the version and release it again"


def test_a_later_revision_in_the_same_month_sorts_after_an_earlier_one() -> None:
    versions = ["2026.10.10", "2026.11.0", "2026.10.2", "2026.10.9"]

    assert sorted(versions, key=version_sort_key) == [
        "2026.10.2",
        "2026.10.9",
        "2026.10.10",
        "2026.11.0",
    ]


def test_every_released_version_is_still_compatible() -> None:
    current = mammal_traits()

    for version, table in released_tables().items():
        assert compatibility_problems(table, current) == [], version


def test_a_removed_or_changed_code_is_a_compatibility_problem() -> None:
    data = _table()
    data["traits"] = data["traits"][1:]
    removed = _load(data)
    data = _table()
    data["traits"][0]["stored_unit"] = "kg"
    data["traits"][0]["printed_units"] = {"kg": "1"}
    changed = _load(data)
    first = data["traits"][0]["code"]

    assert compatibility_problems(mammal_traits(), removed) == [
        f"{first} was removed, deprecate it instead"
    ]
    assert any(
        "stored unit" in problem for problem in compatibility_problems(mammal_traits(), changed)
    )


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


def test_a_vt_id_is_only_on_a_code_that_names_the_same_measurement() -> None:
    # VT body length is a specified measurement. An unspecified length is not that term.
    body_length = mammal_traits().get("body_length")

    assert body_length is not None
    assert body_length.trait_id.startswith("BPT:")


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
    litter = mammal_traits().get("litter_size")

    assert isinstance(teats, CountTrait)
    assert teats.stored_unit == "teats"
    assert isinstance(litter, CountTrait)
    assert litter.stored_unit == "young"


def test_a_count_must_name_its_unit() -> None:
    data = _table()
    count = next(trait for trait in data["traits"] if trait["kind"] == "count")
    del count["stored_unit"]

    with pytest.raises(ValidationError, match="stored_unit"):
        _load(data)


def test_gestation_is_not_the_implantation_term() -> None:
    gestation = mammal_traits().get("gestation_length")

    assert gestation is not None
    assert gestation.trait_id.startswith("BPT:")


def test_lengths_that_cannot_be_converted_are_separate_codes() -> None:
    table = mammal_traits()
    pairs = (
        ("hind_foot_length", "hind_foot_length_with_claw"),
        ("greatest_skull_length", "greatest_skull_length_excluding_incisors"),
        ("greatest_skull_length", "skull_length"),
        ("upper_diastema_length", "lower_diastema_length"),
    )

    for left_code, right_code in pairs:
        left, right = table.get(left_code), table.get(right_code)
        assert left is not None and right is not None
        assert left.trait_id != right.trait_id


def test_an_activity_phrase_from_the_middle_bin_is_a_level() -> None:
    cycle = mammal_traits().get("activity_cycle")

    assert isinstance(cycle, CategoricalTrait)
    assert cycle.level_for("nocturnal/crepuscular") == "nocturnal/crepuscular"
    assert cycle.level_for("diurnal/crepuscular") == "diurnal/crepuscular"


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
