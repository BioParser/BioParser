"""Convert a printed trait value with the trait list. The list owns units and levels."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from bioparser.ets.notices import (
    Approximate,
    Notice,
    NotInVocabulary,
    NotNumeric,
    NotOnList,
    UnitRefused,
)
from bioparser.ets.numbers import canonical_decimal, parse_count
from bioparser.traits.table import (
    CategoricalTrait,
    NumericTrait,
    TraitTable,
    mammal_traits,
)


@dataclass(frozen=True)
class ResolvedTraitValue:
    """A trait value as the list stores it, with its name and identifier.

    ``unit`` is None for a categorical trait. ``warnings`` are what the resolution found.
    """

    value: str
    unit: str | None
    trait_name: str
    trait_id: str
    kind: Literal["numeric", "count", "categorical"]
    warnings: tuple[Notice, ...] = ()


def resolve_trait_value(
    trait_code: str, value: str, unit: str, *, aggregate: bool = False
) -> ResolvedTraitValue | Notice:
    """The trait value as the list stores it, with its name and id, or the notice for why not.

    The notice is ``NotOnList`` for a code that is not on the list, ``NotInVocabulary`` for
    a level that is not one of the trait's levels, ``UnitRefused`` for a unit the trait does not
    accept, and ``NotNumeric`` for a value that is not a number.

    ``trait_code`` is the internal code from the artifact's ``traitCode``, such as ``body_mass``.
    ``aggregate`` is True when the paper prints a mean or a median, which lets a count be a
    decimal such as a litter size of 3.5.
    """
    table = mammal_traits()
    entry = table.get(trait_code)
    if entry is None:
        return NotOnList()
    warnings: list[Notice] = []
    if isinstance(entry, NumericTrait):
        return _convert_numeric(table, entry, value, unit, warnings)
    # A count and a level are not converted. A measurement unit that is not this entry's own
    # unit is evidence the code is wrong, so the claim does not hold. The entry's own unit does.
    if table.knows_unit(unit) and not table.accepts_unit(trait_code, unit):
        return UnitRefused(kind="unit_not_for_trait")
    if isinstance(entry, CategoricalTrait):
        level = table.level_for(trait_code, value)
        if level is None:
            return NotInVocabulary(field="traitValue")
        return ResolvedTraitValue(
            level, None, entry.name, entry.trait_id, "categorical", tuple(warnings)
        )
    count = parse_count(value)
    rendered = str(count) if count is not None else canonical_decimal(value) if aggregate else None
    if rendered is None:
        return NotNumeric(field="traitValue")
    return ResolvedTraitValue(
        rendered, entry.trait_unit, entry.name, entry.trait_id, "count", tuple(warnings)
    )


def accepts_unit(trait_code: str, unit: str) -> bool:
    """True when the trait takes this printed unit as it is, with no reading of it."""
    return mammal_traits().accepts_unit(trait_code, unit)


def _convert_numeric(
    table: TraitTable, entry: NumericTrait, value: str, unit: str, warnings: list[Notice]
) -> ResolvedTraitValue | Notice:
    factor = entry.factor_for(unit, table.unit_aliases)
    if factor is None:
        return UnitRefused(kind="unit_not_for_trait" if table.knows_unit(unit) else "unit_unknown")
    rendered = canonical_decimal(value)
    if rendered is None:
        return NotNumeric(field="traitValue")
    canonical = canonical_decimal(format(Decimal(rendered) * factor, "f"))
    if canonical is None:
        return NotNumeric(field="traitValue")
    if entry.is_approximate(unit, table.unit_aliases):
        warnings.append(Approximate())
    return ResolvedTraitValue(
        canonical, entry.trait_unit, entry.name, entry.trait_id, "numeric", tuple(warnings)
    )
