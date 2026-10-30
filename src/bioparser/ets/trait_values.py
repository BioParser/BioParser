"""Convert a printed trait value with the trait list. The list owns units and levels."""

from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from typing import Literal

from bioparser.ets.numbers import canonical_decimal, parse_count
from bioparser.ets.traits import CategoricalTrait, CountTrait, NumericTrait, mammal_traits

TraitProblem = Literal[
    "trait_unknown",
    "unit_unknown",
    "unit_not_for_trait",
    "value_not_numeric",
    "level_unknown",
]


@dataclass(frozen=True)
class ResolvedTraitValue:
    """A trait value as the list stores it, with its name, identifier and conversion factor.

    ``unit`` is None for a categorical trait. ``factor`` is 1 for a count and a level.
    """

    value: str
    unit: str | None
    factor: Decimal
    trait_name: str
    trait_id: str


def resolve_trait_value(
    trait_code: str, value: str, unit: str
) -> ResolvedTraitValue | TraitProblem:
    """The trait value as the list stores it, with its name and id, or why it cannot be resolved.

    ``trait_code`` is the internal code from the artifact's ``traitCode``, such as ``body_mass``.
    """
    entry = mammal_traits().get(trait_code)
    if entry is None:
        return "trait_unknown"
    if isinstance(entry, CategoricalTrait):
        level = entry.level_for(value)
        if level is None:
            return "level_unknown"
        return ResolvedTraitValue(level, None, Decimal(1), entry.name, entry.trait_id)
    if isinstance(entry, CountTrait):
        count = parse_count(value)
        if count is None:
            return "value_not_numeric"
        return ResolvedTraitValue(
            str(count), entry.stored_unit, Decimal(1), entry.name, entry.trait_id
        )
    return _convert_numeric(entry, value, unit)


def _convert_numeric(
    entry: NumericTrait, value: str, unit: str
) -> ResolvedTraitValue | TraitProblem:
    factor = entry.factor_for(unit)
    if factor is None:
        return "unit_not_for_trait" if unit in _known_units() else "unit_unknown"
    rendered = canonical_decimal(value)
    if rendered is None:
        return "value_not_numeric"
    canonical = canonical_decimal(format(Decimal(rendered) * factor, "f"))
    if canonical is None:
        return "value_not_numeric"
    return ResolvedTraitValue(canonical, entry.stored_unit, factor, entry.name, entry.trait_id)


@cache
def _known_units() -> frozenset[str]:
    """Every printed unit any numeric trait accepts."""
    return frozenset(
        unit
        for entry in mammal_traits().traits
        if isinstance(entry, NumericTrait)
        for unit in entry.printed_units
    )


def scale_measure(text: str, factor: Decimal, *, squared: bool) -> float:
    """A printed decimal times the trait factor, or times the square of that factor.

    ``text`` is a decimal the caller has already accepted. Variance uses the square.
    A standard deviation, a standard error, a minimum, and a maximum use the factor once.
    """
    multiplier = factor * factor if squared else factor
    return float(Decimal(text) * multiplier)
