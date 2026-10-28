"""Units this adapter accepts. The extractor keeps a separate copy."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from bioparser.ets.numbers import canonical_decimal

Unit = Literal["g", "kg", "mm", "cm", "m"]
TargetUnit = Literal["g", "mm"]
UnitProblem = Literal["trait_unknown", "unit_unknown", "unit_not_for_trait", "value_not_numeric"]

# Mass is grams. Length is millimetres. This list is the trait check until a thesaurus exists.
TRAIT_UNITS: dict[str, frozenset[Unit]] = {
    "body_mass": frozenset({"g", "kg"}),
    "body_length": frozenset({"mm", "cm", "m"}),
    "tail_length": frozenset({"mm", "cm", "m"}),
}

_FACTOR: dict[str, Decimal] = {
    "g": Decimal(1),
    "kg": Decimal(1000),
    "mm": Decimal(1),
    "cm": Decimal(10),
    "m": Decimal(1000),
}


@dataclass(frozen=True)
class CanonicalTraitValue:
    """A trait value in its canonical unit, with the factor that put it there."""

    value: str
    unit: TargetUnit
    factor: Decimal


def convert_trait_value_to_canonical(
    trait: str, value: str, unit: str
) -> CanonicalTraitValue | UnitProblem:
    """Resolved trait value in grams or millimetres, or why it cannot be converted."""
    factor = _factor_for(trait, unit)
    if not isinstance(factor, Decimal):
        return factor
    rendered = canonical_decimal(value)
    if rendered is None:
        return "value_not_numeric"
    converted = Decimal(rendered) * factor
    canonical = canonical_decimal(format(converted, "f"))
    if canonical is None:
        return "value_not_numeric"
    unit_out: TargetUnit = "g" if trait == "body_mass" else "mm"
    return CanonicalTraitValue(canonical, unit_out, factor)


def scale_measure(text: str, factor: Decimal, *, squared: bool) -> float:
    """A printed decimal times the trait factor, or times the square of that factor.

    ``text`` is a decimal the caller has already accepted. Variance uses the square.
    A standard deviation, a standard error, a minimum, and a maximum use the factor once.
    """
    multiplier = factor * factor if squared else factor
    return float(Decimal(text) * multiplier)


def _factor_for(trait: str, unit: str) -> Decimal | UnitProblem:
    allowed = TRAIT_UNITS.get(trait)
    if allowed is None:
        return "trait_unknown"
    if unit not in _FACTOR:
        return "unit_unknown"
    if unit not in allowed:
        return "unit_not_for_trait"
    return _FACTOR[unit]
