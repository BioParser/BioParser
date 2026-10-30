"""Convert a printed trait value with the trait list. The list owns units and levels."""

from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from typing import Literal

from bioparser.ets.notices import Approximate, DeprecatedTrait, Notice, UnitNotExpected
from bioparser.ets.numbers import canonical_decimal, parse_count
from bioparser.ets.traits import CategoricalTrait, NumericTrait, mammal_traits

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
) -> ResolvedTraitValue | TraitProblem:
    """The trait value as the list stores it, with its name and id, or why it cannot be resolved.

    ``trait_code`` is the internal code from the artifact's ``traitCode``, such as ``body_mass``.
    ``aggregate`` is True when the paper prints a mean or a median, which lets a count be a
    decimal such as a litter size of 3.5.
    """
    entry = mammal_traits().get(trait_code)
    if entry is None:
        return "trait_unknown"
    warnings: list[Notice] = []
    if entry.status == "deprecated":
        warnings.append(DeprecatedTrait())
    if isinstance(entry, NumericTrait):
        return _convert_numeric(entry, value, unit, warnings)
    # A count and a level are not converted, so a printed unit is not checked. A real unit
    # next to one suggests the observation names the wrong trait.
    if unit in _known_units():
        warnings.append(UnitNotExpected())
    if isinstance(entry, CategoricalTrait):
        level = entry.level_for(value)
        if level is None:
            return "level_unknown"
        return ResolvedTraitValue(
            level, None, Decimal(1), entry.name, entry.trait_id, "categorical", tuple(warnings)
        )
    count = parse_count(value)
    rendered = str(count) if count is not None else canonical_decimal(value) if aggregate else None
    if rendered is None:
        return "value_not_numeric"
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
    if unit in entry.approximate_units:
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
