from bioparser.traits.table import mammal_traits
from bioparser.traits.traitlist import iri_for, traitlist_rows


def test_each_kind_maps_onto_an_ets_value_type() -> None:
    rows = {row.trait: row for row in traitlist_rows()}
    mass = rows["Body mass"]
    litter = rows["Litter size"]
    cycle = rows["Activity cycle"]

    assert mass.valueType == "numeric" and mass.expectedUnit == "g" and mass.factorLevels is None
    assert litter.valueType == "integer" and litter.expectedUnit == "young"
    assert cycle.valueType == "categorical"
    assert cycle.factorLevels is not None and "nocturnal" in cycle.factorLevels
    assert cycle.expectedUnit is None


def test_a_vt_id_and_a_bpt_id_expand_to_their_prefixes() -> None:
    table = mammal_traits()

    assert iri_for(table, "VT:0001259") == "http://purl.obolibrary.org/obo/VT_0001259"
    assert iri_for(table, "BPT:0000001").startswith(
        "https://bioparser.github.io/BioParser/traits/BPT/"
    )


def test_a_domain_filter_hides_other_traits_and_an_unknown_domain_fails() -> None:
    codes = {row.trait for row in traitlist_rows(domains=("acoustics",))}

    assert "Call peak frequency" in codes
    assert "Body mass" not in codes
    try:
        traitlist_rows(domains=("diet",))
    except ValueError as exc:
        assert "unknown" in str(exc)
    else:
        raise AssertionError("expected an unknown domain")


def test_a_deprecated_trait_is_hidden_unless_requested() -> None:
    import json

    from bioparser.traits.table import load_table

    raw = json.loads(mammal_traits().model_dump_json())
    raw["traits"][0]["status"] = "deprecated"
    table = load_table(json.dumps(raw))
    name = raw["traits"][0]["name"]

    assert name not in {row.trait for row in traitlist_rows(table)}
    assert name in {row.trait for row in traitlist_rows(table, include_deprecated=True)}
