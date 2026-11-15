import json
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from bioparser.traits.table import (
    CategoricalTrait,
    CountTrait,
    NumericTrait,
    json_schema,
    mammal_traits,
)

from .helpers import _load, _table, _with_alias_data


def test_the_shipped_list_loads_and_has_every_value_kind() -> None:
    table = mammal_traits()

    kinds = {type(trait) for trait in table.traits}
    assert kinds == {NumericTrait, CountTrait, CategoricalTrait}
    assert len(table.traits) == 39


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


def test_an_unspecified_body_length_is_not_on_the_list() -> None:
    # Head-body length and total length name the measurement. An unspecified length does not.
    table = mammal_traits()
    head_body = table.get("head_body_length")

    assert table.get("body_length") is None
    assert head_body is not None
    assert head_body.trait_id.startswith("BPT:")


def test_head_body_and_total_length_are_separate_traits() -> None:
    table = mammal_traits()
    head_body = table.get("head_body_length")
    total = table.get("total_length")

    assert head_body is not None and total is not None
    assert head_body.trait_id != total.trait_id


def test_select_expands_domains_and_codes_and_rejects_unknown_names() -> None:
    table = mammal_traits()

    chosen = table.select(domains=("skeleton",), codes=("body_mass",))
    assert "greatest_skull_length" in chosen and "body_mass" in chosen
    with pytest.raises(ValueError, match="unknown"):
        table.select(domains=("diet",))
    with pytest.raises(ValueError, match="unknown"):
        table.select(codes=("wing_length",))


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
        (lambda d: d["traits"][0].update(trait_id="BPT:12"), "trait id"),
        (
            lambda d: d["traits"][0].update(
                trait_id="BPT:\u0660\u0660\u0660\u0660\u0660\u0660\u0661"
            ),
            "trait id",
        ),
        (lambda d: d["id_prefixes"].update({"bad prefix": None}), "letters only"),
        (lambda d: d["id_prefixes"].update({"A:B": None}), "letters only"),
        (lambda d: d["id_prefixes"].update({"VT": "purl.obolibrary.org/obo/VT_"}), "http"),
        (lambda d: d["traits"][0].update(code="Body Mass"), "code"),
        (lambda d: d["traits"][0]["printed_units"].update(kg="0"), "positive"),
        (lambda d: d["traits"][0]["printed_units"].update(kg="heavy"), "decimal"),
        (lambda d: d["traits"][0].update(trait_unit="lb"), "trait unit"),
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
    assert teats.trait_unit == "teats"
    assert isinstance(litter, CountTrait)
    assert litter.trait_unit == "young"


def test_a_count_must_name_its_unit() -> None:
    data = _table()
    count = next(trait for trait in data["traits"] if trait["kind"] == "count")
    del count["trait_unit"]

    with pytest.raises(ValidationError, match="trait_unit"):
        _load(data)


def test_gestation_is_not_the_implantation_term() -> None:
    gestation = mammal_traits().get("gestation_length")

    assert gestation is not None
    assert gestation.trait_id.startswith("BPT:")


def test_lengths_that_cannot_be_converted_are_separate_codes() -> None:
    table = mammal_traits()
    pairs = (
        ("hind_foot_length", "hind_foot_length_with_claw"),
        ("greatest_skull_length", "condylobasal_length"),
        ("head_body_length", "neonate_head_body_length"),
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

    with pytest.raises(ValidationError, match="approximate units"):
        _load(data)


def test_a_categorical_trait_needs_distinct_levels() -> None:
    data = _table()
    categorical = next(trait for trait in data["traits"] if trait["kind"] == "categorical")
    categorical["levels"] = ["yes", "Yes"]

    with pytest.raises(ValidationError, match="distinct"):
        _load(data)


def test_life_stage_stays_a_column_and_birth_sizes_are_their_own_traits() -> None:
    table = mammal_traits()

    for code in ("neonate_body_mass", "weaning_body_mass", "age_at_sexual_maturity_female"):
        assert table.get(code) is None
    assert table.get("neonate_mass") is not None
    assert table.get("weaning_mass") is not None
    assert table.get("body_mass") is not None
    assert table.get("age_at_sexual_maturity") is not None
    assert table.get("age_at_first_reproduction") is not None
    assert table.get("age_at_first_reproduction_female") is None


def test_a_count_of_animals_is_stored_as_individuals() -> None:
    for code in ("social_group_size",):
        entry = mammal_traits().get(code)
        assert isinstance(entry, NumericTrait)
        assert entry.trait_unit == "individuals"
        assert mammal_traits().factor_for(code, "animals") == 1


def test_spellings_of_one_unit_cannot_both_be_printed_units() -> None:
    data = _table()
    mass = next(trait for trait in data["traits"] if trait["code"] == "body_mass")
    mass["printed_units"]["grams"] = "1"

    with pytest.raises(ValidationError, match="same unit"):
        _load(data)


def test_levels_that_differ_only_in_separators_are_not_distinct() -> None:
    data = _table()
    categorical = next(trait for trait in data["traits"] if trait["kind"] == "categorical")
    categorical["levels"] = ["non-migratory", "non migratory"]

    with pytest.raises(ValidationError, match="distinct"):
        _load(data)


def test_the_json_schema_describes_every_kind() -> None:
    schema = json.dumps(json_schema())

    for name in ("NumericTrait", "CountTrait", "CategoricalTrait"):
        assert name in schema


@pytest.mark.parametrize("printed", ["C", "°C", "ºC", "℃", "deg C", "Celsius", "degrees Celsius"])
def test_every_spelling_of_celsius_is_the_trait_unit(printed: str) -> None:
    entry = mammal_traits().get("body_temperature")

    assert isinstance(entry, NumericTrait)
    assert entry.trait_unit == "°C"
    assert mammal_traits().factor_for("body_temperature", printed) == 1


def test_a_litter_size_is_a_whole_count_unless_the_paper_prints_an_average() -> None:
    from bioparser.ets.trait_values import resolve_trait_value

    whole = resolve_trait_value("litter_size", "3", "")
    average = resolve_trait_value("litter_size", "3.5", "", aggregate=True)
    single_decimal = resolve_trait_value("litter_size", "3.5", "")

    assert getattr(whole, "value", None) == "3"
    assert getattr(average, "value", None) == "3.5"
    assert not hasattr(single_decimal, "value")


def test_levels_accept_the_forms_papers_print() -> None:
    table = mammal_traits()
    torpor = table.get("hibernation_torpor")
    migration = table.get("migratory_status")

    assert isinstance(torpor, CategoricalTrait) and isinstance(migration, CategoricalTrait)
    assert table.level_for("hibernation_torpor", "N") == "no"
    assert table.level_for("hibernation_torpor", "Y") == "torpor"
    assert table.level_for("hibernation_torpor", "yes") == "torpor"
    assert table.level_for("migratory_status", "yes") == "yes"
    assert table.level_for("hibernation_torpor", "Hibernation") == "hibernation"
    assert table.level_for("migratory_status", "Partial") == "partial"
    assert table.level_for("migratory_status", "Y") == "yes"


@pytest.mark.parametrize(
    ("code", "printed", "factor"),
    [
        ("body_mass", "lbs", "453.59237"),
        ("body_mass", "tonnes", "1000000"),
        ("tail_length", "inches", "25.4"),
        ("head_body_length", "feet", "304.8"),
        ("greatest_skull_length", "m", "1000"),
        ("individual_home_range_size", "sq mi", "2.589988110336"),
        ("individual_home_range_size", "acres", "0.0040468564224"),
        ("population_density", "individuals m-2", "1000000"),
        ("population_density", "individuals km⁻²", "1"),
        ("population_density", "individuals ha⁻¹", "100"),
        ("population_density", "ind. ha-1", "100"),
        ("basal_metabolic_rate", "kcal/h", "4.184"),
        ("basal_metabolic_rate", "mW", "0.0036"),
    ],
)
def test_printed_units_from_older_and_special_papers_convert(
    code: str, printed: str, factor: str
) -> None:
    from decimal import Decimal

    entry = mammal_traits().get(code)

    assert isinstance(entry, NumericTrait)
    assert mammal_traits().factor_for(code, printed) == Decimal(factor)


def test_a_level_alias_must_name_a_level_and_not_repeat_one() -> None:
    for alias, message in (({"yes": "nothing"}, "unknown level"), ({"no": "torpor"}, "already")):
        data = _table()
        trait = next(t for t in data["traits"] if t["code"] == "hibernation_torpor")
        trait["level_aliases"] = alias

        with pytest.raises(ValidationError, match=message):
            _load(data)


def test_the_list_names_every_alias_it_accepts() -> None:
    table = mammal_traits()

    assert table.unit_aliases["lbs"] == "lb"
    assert table.unit_aliases["deg C"] == "°C"
    assert table.level_aliases["y"] == "yes"


def test_a_list_wide_alias_may_name_something_no_trait_uses() -> None:
    units = _with_alias_data(unit_aliases={"smoots": "smoot"})
    levels = _with_alias_data(level_aliases={"maybe": "perhaps"})

    assert _load(units).unit_aliases["smoots"] == "smoot"
    assert _load(levels).level_aliases["maybe"] == "perhaps"


def test_a_trait_unit_alias_applies_to_that_trait_only() -> None:
    data = _table()
    mass = next(t for t in data["traits"] if t["code"] == "body_mass")
    mass["unit_aliases"] = {"kilos": "kg"}
    table = _load(data)

    assert table.factor_for("body_mass", "kilos") == 1000
    assert table.factor_for("brain_mass", "kilos") is None


def test_a_trait_unit_alias_must_name_one_of_its_units() -> None:
    data = _table()
    mass = next(t for t in data["traits"] if t["code"] == "body_mass")
    mass["unit_aliases"] = {"stone": "st"}

    with pytest.raises(ValidationError, match="does not print"):
        _load(data)


def test_a_trait_can_switch_off_a_list_wide_unit_alias() -> None:
    data = _table()
    gestation = next(t for t in data["traits"] if t["code"] == "gestation_length")
    gestation["unit_aliases"] = {"d": "week"}
    table = _load(data)

    assert table.factor_for("gestation_length", "d") == 7
    assert table.factor_for("weaning_age", "d") == 1


def test_a_trait_level_alias_wins_over_a_list_wide_alias() -> None:
    data = _table()
    migration = next(t for t in data["traits"] if t["code"] == "migratory_status")
    migration["level_aliases"] = {"y": "partial"}
    table = _load(data)

    assert table.level_for("migratory_status", "Y") == "partial"
    assert table.level_for("migratory_status", "yes") == "yes"


def test_a_count_does_not_let_a_folded_alias_capture_another_unit() -> None:
    data = _table()
    data["unit_aliases"]["mg"] = "young"
    table = _load(data)

    assert table.accepts_unit("litter_size", "mg")
    assert table.accepts_unit("litter_size", "YOUNG")
    assert not table.accepts_unit("litter_size", "MG")
    assert not table.accepts_unit("litter_size", "Mg")


def test_a_list_wide_unit_alias_can_name_a_count_unit() -> None:
    data = _table()
    data["unit_aliases"]["pups"] = "young"
    table = _load(data)

    assert table.accepts_unit("litter_size", "Pups")
    assert not table.accepts_unit("teat_number", "pups")
    assert table.knows_unit("pups")


def test_one_lookup_resolves_a_level_with_no_chaining() -> None:
    table = mammal_traits()

    assert table.level_for("hibernation_torpor", "Y") == "torpor"
    assert table.level_for("hibernation_torpor", "true") is None
    assert table.level_for("migratory_status", "true") == "yes"


def test_a_list_wide_level_alias_may_not_take_the_name_of_a_level() -> None:
    data = _with_alias_data(level_aliases={"no": "yes"})

    with pytest.raises(ValidationError, match="name of a level"):
        _load(data)


def test_a_trait_may_keep_a_level_that_a_list_wide_alias_would_take() -> None:
    data = _table()
    data["level_aliases"]["partial"] = "yes"
    with pytest.raises(ValidationError, match="map it to itself"):
        _load(data)

    migration = next(t for t in data["traits"] if t["code"] == "migratory_status")
    migration["level_aliases"] = {"partial": "partial"}
    table = _load(data)

    assert table.level_for("migratory_status", "partial") == "partial"


def test_a_level_may_only_be_aliased_to_itself() -> None:
    data = _table()
    migration = next(t for t in data["traits"] if t["code"] == "migratory_status")
    migration["level_aliases"] = {"no": "yes"}

    with pytest.raises(ValidationError, match="already a level"):
        _load(data)


def test_a_compound_unit_uses_an_alias_on_one_side() -> None:
    table = mammal_traits()

    assert table.factor_for("litters_per_year", "litters/yr") == 1
    assert table.factor_for("population_density", "individuals/hectares") == 100


def test_maximum_longevity_is_stored_in_years() -> None:
    table = mammal_traits()

    assert table.factor_for("maximum_longevity", "years") == 1
    assert table.is_approximate("maximum_longevity", "days")
    assert not table.is_approximate("maximum_longevity", "years")


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d["unit_aliases"].update({"sqmi": "mi2"}),
        lambda d: d["level_aliases"].update({"Y": "no"}),
        lambda d: next(t for t in d["traits"] if t["code"] == "body_mass").update(
            unit_aliases={"kilos": "kg", "kilos.": "g"}
        ),
        lambda d: next(t for t in d["traits"] if t["code"] == "hibernation_torpor").update(
            level_aliases={"y": "torpor", "Y": "hibernation"}
        ),
    ],
)
def test_two_spellings_that_normalize_alike_cannot_both_be_aliases(edit: Any) -> None:
    data = _table()
    edit(data)

    with pytest.raises(ValidationError, match="same spelling"):
        _load(data)


@pytest.mark.parametrize(
    ("printed", "factor"),
    [
        ("mg", "0.001"),
        ("Mg", "1000000"),
        ("MG", "0.001"),
        ("Kg", "1000"),
        ("KG", "1000"),
        ("T", "1000000"),
    ],
)
def test_case_decides_a_unit_first_and_is_ignored_only_when_nothing_matches(
    printed: str, factor: str
) -> None:
    from decimal import Decimal

    assert mammal_traits().factor_for("body_mass", printed) == Decimal(factor)


def test_an_alias_and_a_printed_unit_may_differ_only_by_case() -> None:
    data = _table()
    data["unit_aliases"]["Kilos"] = "g"
    table = _load(data)

    assert table.factor_for("body_mass", "Kilos") == 1
    assert table.factor_for("body_mass", "KILOS") == 1
