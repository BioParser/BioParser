"""The trait list as an ETS v0.10 Traitlist table.

``valueType`` uses the ETS vocabulary: ``numeric`` for a measurement, ``integer`` for a
count, and ``categorical`` for a level. A count is a whole number. A mean or a median of a
count may be a decimal, and the row stays ``integer``. ``factorLevels`` is the
comma-separated list ETS specifies. A categorical value stays ``categorical`` even when
the levels are yes and no, because the stored text is those words and not ``TRUE`` or
``FALSE``.
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


class EtsTraitList(BaseModel):
    """The trait list as ETS Traitlist rows, plus the list version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    traits: tuple[TraitlistRow, ...]


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
) -> tuple[NumericTrait | CountTrait | CategoricalTrait, ...]:
    """Traits in the named domains, plus the named codes. Neither means the whole list."""
    if not domains and not codes:
        return table.traits
    chosen = set(table.select(domains=domains, codes=codes))
    return tuple(trait for trait in table.traits if trait.code in chosen)


def traitlist_rows(
    table: TraitTable | None = None,
    *,
    domains: tuple[str, ...] = (),
    codes: tuple[str, ...] = (),
) -> tuple[TraitlistRow, ...]:
    """ETS Traitlist rows for the selected traits. The default table is the shipped list."""
    source = mammal_traits() if table is None else table
    return tuple(
        _row(source, trait) for trait in selected_traits(source, domains=domains, codes=codes)
    )


def _row(table: TraitTable, trait: NumericTrait | CountTrait | CategoricalTrait) -> TraitlistRow:
    if isinstance(trait, CategoricalTrait):
        return TraitlistRow(
            identifier=iri_for(table, trait.trait_id),
            trait=trait.name,
            valueType="categorical",
            factorLevels=", ".join(trait.levels),
            traitDescription=trait.definition,
        )
    return TraitlistRow(
        identifier=iri_for(table, trait.trait_id),
        trait=trait.name,
        valueType="integer" if isinstance(trait, CountTrait) else "numeric",
        expectedUnit=trait.trait_unit,
        traitDescription=trait.definition,
    )
