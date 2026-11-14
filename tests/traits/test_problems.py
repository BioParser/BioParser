from typing import Any

import pytest

from bioparser.traits.table import list_problems

from .helpers import _problems, _table, _with_alias_data


def test_a_sound_list_has_no_problems() -> None:
    assert _problems(_table()) == []


def test_a_list_that_is_not_json_says_so() -> None:

    [problem] = list_problems("{")

    assert problem.rule == "not_json"
    assert problem.message.startswith("The list is not valid JSON")


def test_every_problem_is_reported_not_only_the_first() -> None:
    data = _table()
    data["traits"][1]["code"] = data["traits"][0]["code"]
    data["traits"][2]["domain"] = "nowhere"

    rules = {p.rule for p in _problems(data)}

    assert rules >= {"duplicate_code", "unknown_domain"}


def test_a_problem_names_the_trait_the_field_and_the_spelling() -> None:
    data = _table()
    mass = next(t for t in data["traits"] if t["code"] == "body_mass")
    mass["unit_aliases"] = {"stone": "st"}

    [problem] = _problems(data)

    assert (problem.rule, problem.trait, problem.field, problem.spelling) == (
        "unit_alias_target_not_printed",
        "body_mass",
        "unit_aliases",
        "stone",
    )
    assert problem.message == (
        "body_mass has the unit alias 'stone' for 'st', but the trait does not print that unit."
    )


def test_a_shape_error_is_a_problem_with_its_own_rule_trait_field_and_spelling() -> None:
    data = _table()
    mass = next(t for t in data["traits"] if t["code"] == "body_mass")
    mass["printed_units"]["kg"] = "heavy"

    [problem] = _problems(data)

    assert (problem.rule, problem.trait, problem.field, problem.spelling) == (
        "invalid_factor",
        "body_mass",
        "printed_units",
        "kg",
    )
    assert problem.message == (
        "body_mass has the factor 'heavy' for 'kg', which must be a positive decimal number."
    )


@pytest.mark.parametrize(
    ("edit", "rule", "trait", "field"),
    [
        (lambda d: d["traits"][0].update(code="Body Mass"), "invalid_code", "Body Mass", "code"),
        (lambda d: d["traits"][0].update(name=""), "empty_text", "body_mass", "name"),
        (lambda d: d["traits"][0].update(definition="  "), "empty_text", "body_mass", "definition"),
        (lambda d: d["domains"][0].update(name=""), "empty_text", None, "name"),
        (lambda d: d["domains"][0].update(definition=""), "empty_text", None, "definition"),
        (
            lambda d: d["traits"][0].update(trait_id="BPT:12"),
            "invalid_trait_id",
            "body_mass",
            "trait_id",
        ),
        (
            lambda d: d["traits"][0].update(printed_units={}),
            "no_printed_units",
            "body_mass",
            "printed_units",
        ),
        (
            lambda d: next(t for t in d["traits"] if t["code"] == "activity_cycle").update(
                levels=["day", "Day"]
            ),
            "levels_not_distinct",
            "activity_cycle",
            "levels",
        ),
        (lambda d: d["id_prefixes"].update({"a b": None}), "invalid_prefix", None, "id_prefixes"),
        (lambda d: d["id_prefixes"].update({"VT": "ftp://x"}), "invalid_iri", None, "id_prefixes"),
    ],
)
def test_each_shape_rule_names_the_trait_and_the_field(
    edit: Any, rule: str, trait: str | None, field: str
) -> None:
    data = _table()
    edit(data)

    problems = [p for p in _problems(data) if p.rule == rule]

    assert [(p.trait, p.field) for p in problems] == [(trait, field)]


def test_a_subset_of_traits_keeps_list_wide_aliases_nothing_uses() -> None:
    data = _table()
    data["traits"] = [data["traits"][0]]

    assert _problems(data) == []


def test_a_list_wide_level_alias_that_takes_a_level_says_what_to_add() -> None:
    data = _with_alias_data(level_aliases={**_table()["level_aliases"], "partial": "yes"})

    [problem] = _problems(data)

    assert problem.rule == "level_alias_takes_a_level"
    assert problem.trait == "migratory_status"
    assert "'partial': 'partial' in its level aliases" in problem.message


def test_any_prefix_with_an_entry_in_the_prefix_table_is_a_valid_id() -> None:
    data = _table()
    data["id_prefixes"]["UBERON"] = "http://purl.obolibrary.org/obo/UBERON_"
    data["traits"][0]["trait_id"] = "UBERON:0001234"

    assert _problems(data) == []


def test_a_blank_name_is_reported_with_a_bad_code() -> None:
    data = _table()
    data["traits"][0]["code"] = "Body Mass"
    data["traits"][0]["name"] = " "
    data["traits"][0]["definition"] = ""

    problems = [
        p for p in _problems(data) if p.trait == "Body Mass" or p.field in {"name", "definition"}
    ]

    assert [(p.rule, p.field, p.message) for p in problems] == [
        (
            "invalid_code",
            "code",
            "The trait code 'Body Mass' must be lowercase words joined by underscores, like body_mass.",
        ),
        ("empty_text", "name", "Body Mass has an empty name."),
        ("empty_text", "definition", "Body Mass has an empty definition."),
    ]


def test_a_blank_domain_name_and_definition_are_both_reported() -> None:
    data = _table()
    data["domains"][0]["name"] = " "
    data["domains"][0]["definition"] = ""

    problems = [p for p in _problems(data) if p.rule == "empty_text"]

    assert [(p.trait, p.field, p.message) for p in problems] == [
        (None, "name", "A domain has an empty name."),
        (None, "definition", "A domain has an empty definition."),
    ]


def test_a_prefix_without_an_entry_says_where_to_add_it() -> None:
    data = _table()
    data["traits"][0]["trait_id"] = "ZZ:0000001"

    [problem] = _problems(data)

    assert problem.rule == "undeclared_prefix"
    assert problem.message.endswith("Add the prefix there, with its IRI or null.")
