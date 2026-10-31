"""The trait list as an ETS v0.10 Traitlist table.

``valueType`` uses the ETS vocabulary: ``numeric`` for a measurement, ``integer`` for a
count, and ``categorical`` for a level. ``factorLevels`` is the comma-separated list ETS
specifies. A categorical value stays ``categorical`` even when the levels are yes and no,
because the stored text is those words and not ``TRUE`` or ``FALSE``.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from bioparser.traits.table import (
    CategoricalTrait,
    CountTrait,
    NumericTrait,
    TraitTable,
    mammal_traits,
)

ValueType = Literal["numeric", "integer", "categorical"]


class TraitlistRow(BaseModel):
    """One ETS v0.10 Traitlist row. Empty columns are null."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    identifier: str
    trait: str
    broaderTerm: str | None = None
    narrowerTerm: str | None = None
    relatedTerm: str | None = None
    valueType: ValueType
    expectedUnit: str | None = None
    factorLevels: str | None = None
    maxAllowedValue: str | None = None
    minAllowedValue: str | None = None
    traitDescription: str
    comments: str | None = None
    source: str | None = None


def iri_for(table: TraitTable, trait_id: str) -> str:
    """The IRI for an id, or the id itself when its prefix does not resolve yet."""
    prefix, _, local = trait_id.partition(":")
    base = table.id_prefixes.get(prefix)
    if not base:
        return trait_id
    return f"{base}{local}"


def selected_traits(
    table: TraitTable,
    *,
    domains: tuple[str, ...] = (),
    codes: tuple[str, ...] = (),
    include_deprecated: bool = False,
) -> tuple[NumericTrait | CountTrait | CategoricalTrait, ...]:
    """Traits in the named domains, plus the named codes. An unknown name is an error.

    Deprecated traits are left out unless ``include_deprecated`` is set. A deprecated code
    named in ``codes`` is included either way.
    """
    known_domains = {domain.name for domain in table.domains}
    unknown_domains = [name for name in domains if name not in known_domains]
    known_codes = {trait.code for trait in table.traits}
    unknown_codes = [code for code in codes if code not in known_codes]
    if unknown_domains or unknown_codes:
        raise ValueError(f"unknown domains {unknown_domains}, unknown codes {unknown_codes}")
    named = set(codes)
    chosen: list[NumericTrait | CountTrait | CategoricalTrait] = []
    for trait in table.traits:
        in_domain = bool(domains) and trait.domain in domains
        picked = (not domains and not named) or in_domain or trait.code in named
        if not picked:
            continue
        if trait.status == "deprecated" and not include_deprecated and trait.code not in named:
            continue
        chosen.append(trait)
    return tuple(chosen)


def traitlist_rows(
    table: TraitTable | None = None,
    *,
    domains: tuple[str, ...] = (),
    codes: tuple[str, ...] = (),
    include_deprecated: bool = False,
) -> tuple[TraitlistRow, ...]:
    """ETS Traitlist rows for the selected traits. The default table is the shipped list."""
    source = mammal_traits() if table is None else table
    return tuple(
        _row(source, trait)
        for trait in selected_traits(
            source, domains=domains, codes=codes, include_deprecated=include_deprecated
        )
    )


def _row(table: TraitTable, trait: NumericTrait | CountTrait | CategoricalTrait) -> TraitlistRow:
    comments = None
    if trait.status == "deprecated":
        replacement = f" Replaced by {trait.replaced_by}." if trait.replaced_by else ""
        comments = f"Deprecated.{replacement}"
    if isinstance(trait, CategoricalTrait):
        return TraitlistRow(
            identifier=iri_for(table, trait.trait_id),
            trait=trait.name,
            valueType="categorical",
            factorLevels=", ".join(trait.levels),
            traitDescription=trait.definition,
            comments=comments,
        )
    if isinstance(trait, CountTrait):
        value_type: ValueType = "integer"
        unit = trait.stored_unit
    else:
        value_type = "numeric"
        unit = trait.stored_unit
    return TraitlistRow(
        identifier=iri_for(table, trait.trait_id),
        trait=trait.name,
        valueType=value_type,
        expectedUnit=unit,
        traitDescription=trait.definition,
        comments=comments,
    )
