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
    """A trait value in its canonical unit."""

    value: str
    unit: TargetUnit


def convert_trait_value_to_canonical(
    trait: str, value: str, unit: str
) -> CanonicalTraitValue | UnitProblem:
    """Resolved trait value in grams or millimetres, or why it cannot be converted."""
    allowed = TRAIT_UNITS.get(trait)
    if allowed is None:
        return "trait_unknown"
    if unit not in _FACTOR:
        return "unit_unknown"
    if unit not in allowed:
        return "unit_not_for_trait"
    rendered = canonical_decimal(value)
    if rendered is None:
        return "value_not_numeric"
    converted = Decimal(rendered) * _FACTOR[unit]
    canonical = canonical_decimal(format(converted, "f"))
    if canonical is None:
        return "value_not_numeric"
    unit_out: TargetUnit = "g" if trait == "body_mass" else "mm"
    return CanonicalTraitValue(canonical, unit_out)
