"""Convert a printed trait value with the trait list. The list owns units and levels."""

from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from typing import Literal

from bioparser.ets.notices import (
    Approximate,
    DeprecatedTrait,
    Notice,
    NotInVocabulary,
    NotNumeric,
    UnitRefused,
    UnmappedTrait,
)
from bioparser.ets.numbers import canonical_decimal, parse_count
from bioparser.traits.normalize import normalize_unit
from bioparser.traits.table import CategoricalTrait, CountTrait, NumericTrait, mammal_traits


@dataclass(frozen=True)
class ResolvedTraitValue:
    """A trait value as the list stores it, with its name, identifier and conversion factor.

    ``unit`` is None for a categorical trait. ``factor`` is 1 for a count and a level.
    ``warnings`` are what the resolution found, such as a deprecated code.
    """

    value: str
    unit: str | None
    factor: Decimal
    trait_name: str
    trait_id: str
    kind: Literal["numeric", "count", "categorical"]
    warnings: tuple[Notice, ...] = ()


def resolve_trait_value(
    trait_code: str, value: str, unit: str, *, aggregate: bool = False
) -> ResolvedTraitValue | Notice:
    """The trait value as the list stores it, with its name and id, or the notice for why not.

    The notice is ``UnmappedTrait`` for a code that is not on the list, ``NotInVocabulary`` for
    a level that is not one of the trait's levels, ``UnitRefused`` for a unit the trait does not
    accept, and ``NotNumeric`` for a value that is not a number.

    ``trait_code`` is the internal code from the artifact's ``traitCode``, such as ``body_mass``.
    ``aggregate`` is True when the paper prints a mean or a median, which lets a count be a
    decimal such as a litter size of 3.5.
    """
    entry = mammal_traits().get(trait_code)
    if entry is None:
        return UnmappedTrait()
    warnings: list[Notice] = []
    if entry.status == "deprecated":
        warnings.append(DeprecatedTrait())
    if isinstance(entry, NumericTrait):
        return _convert_numeric(entry, value, unit, warnings)
    # A count and a level are not converted. A measurement unit that is not this entry's own
    # unit is evidence the code is wrong, so the claim does not hold. The entry's own unit does.
    own_unit = normalize_unit(entry.stored_unit) if isinstance(entry, CountTrait) else None
    if _is_known_unit(unit) and normalize_unit(unit) != own_unit:
        return UnitRefused(kind="unit_not_for_trait")
    if isinstance(entry, CategoricalTrait):
        level = entry.level_for(value)
        if level is None:
            return NotInVocabulary(field="traitValue")
        return ResolvedTraitValue(
            level, None, Decimal(1), entry.name, entry.trait_id, "categorical", tuple(warnings)
        )
    count = parse_count(value)
    rendered = str(count) if count is not None else canonical_decimal(value) if aggregate else None
    if rendered is None:
        return NotNumeric(field="traitValue")
    return ResolvedTraitValue(
        rendered,
        entry.stored_unit,
        Decimal(1),
        entry.name,
        entry.trait_id,
        "count",
        tuple(warnings),
    )


def _convert_numeric(
    entry: NumericTrait, value: str, unit: str, warnings: list[Notice]
) -> ResolvedTraitValue | Notice:
    factor = entry.factor_for(unit)
    if factor is None:
        return UnitRefused(kind="unit_not_for_trait" if _is_known_unit(unit) else "unit_unknown")
    rendered = canonical_decimal(value)
    if rendered is None:
        return NotNumeric(field="traitValue")
    canonical = canonical_decimal(format(Decimal(rendered) * factor, "f"))
    if canonical is None:
        return NotNumeric(field="traitValue")
    if entry.is_approximate(unit):
        warnings.append(Approximate())
    return ResolvedTraitValue(
        canonical,
        entry.stored_unit,
        factor,
        entry.name,
        entry.trait_id,
        "numeric",
        tuple(warnings),
    )


@cache
def _known_units() -> frozenset[str]:
    """Every printed unit any numeric trait accepts, as a spelling-insensitive key."""
    return frozenset(
        normalize_unit(unit)
        for entry in mammal_traits().traits
        if isinstance(entry, NumericTrait)
        for unit in entry.printed_units
    )


def _is_known_unit(unit: str) -> bool:
    return normalize_unit(unit) in _known_units()


def scale_measure(text: str, factor: Decimal, *, squared: bool) -> float:
    """A printed decimal times the trait factor, or times the square of that factor.

    ``text`` is a decimal the caller has already accepted. Variance uses the square.
    A standard deviation, a standard error, a minimum, and a maximum use the factor once.
    """
    multiplier = factor * factor if squared else factor
    return float(Decimal(text) * multiplier)
