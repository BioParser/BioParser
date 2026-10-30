import json
from importlib import resources
from typing import Any, get_args

import pytest
from freeze_traits import load_baseline, snapshot
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


def test_the_current_version_is_frozen_and_unchanged() -> None:
    # Any edit to what a stored result depends on needs a new version. Bump `version`, then
    # run `uv run python tests/ets/freeze_traits.py` in the same change.
    table = mammal_traits()
    baseline = load_baseline()

    assert table.version in baseline, "freeze this version with tests/ets/freeze_traits.py"
    assert snapshot(table) == baseline[table.version], "bump the version and freeze it again"


def test_every_released_trait_still_means_the_same() -> None:
    current = snapshot(mammal_traits())

    for version, released in load_baseline().items():
        for code, old in released.items():
            new = current.get(code)
            assert new is not None, f"{code} from {version} was removed, deprecate it instead"
            assert new["kind"] == old["kind"], f"{code} changed kind"
            # The id is not checked here. It may move to a public ontology term, and that
            # is a release that bumps the version.
            assert new.get("stored_unit") == old.get("stored_unit"), f"{code} changed unit"
            # A definition may be reworded, which needs a new version. Its meaning may not move.
            assert not (old["status"] == "deprecated" and new["status"] == "active"), (
                f"{code} was deprecated and is active again"
            )
            assert set(old.get("approximate_units", ())) <= set(new.get("approximate_units", ())), (
                f"{code} dropped an approximate unit"
            )
            for unit, factor in old.get("printed_units", {}).items():
                assert new["printed_units"].get(unit) == factor, f"{code} changed {unit}"
            assert set(old.get("levels", ())) <= set(new.get("levels", ())), f"{code} lost a level"


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
