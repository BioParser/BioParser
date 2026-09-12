from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Trait = Literal["body_mass", "body_length", "tail_length"]

# A closed set, so guided decoding cannot emit "grams", "g." or "gramme". Free
# text here makes values incomparable and there is no way to repair it later.
Unit = Literal["g", "kg", "mm", "cm", "m"]

# Which units are meaningful for which trait. Checked after parsing rather than
# inside the model: a mismatched pair should cost one observation, not the whole
# payload. See extract.extract_observations.
TRAIT_UNITS: dict[Trait, frozenset[Unit]] = {
    "body_mass": frozenset({"g", "kg"}),
    "body_length": frozenset({"mm", "cm", "m"}),
    "tail_length": frozenset({"mm", "cm", "m"}),
}

# Canonical units: grams for mass, millimetres for length.
_CANONICAL_FACTOR: dict[Unit, float] = {
    "g": 1.0,
    "kg": 1000.0,
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
}


def to_canonical(value: float, unit: Unit) -> float:
    """Value in grams (mass) or millimetres (length)."""
    return value * _CANONICAL_FACTOR[unit]


class Observation(BaseModel):
    """One measurement as the model reports it.

    This is the guided-decoding schema, so every field is something the model
    must emit. The length caps are defence in depth behind max_tokens and are
    deliberately generous: exceeding one fails the whole extraction, not just
    the offending observation.
    """

    model_config = ConfigDict(extra="forbid")

    taxon: str = Field(min_length=1, max_length=120)
    trait: Trait
    # allow_inf_nan: json.loads accepts Infinity and NaN, and neither is a mass.
    value: float = Field(gt=0, allow_inf_nan=False)
    unit: Unit
    evidence_block_id: str = Field(min_length=1, max_length=80)
    quotation: str = Field(min_length=1, max_length=300)


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 8 * ~70 tokens = ~560 tokens; raise --max-model-len before raising this.
    # Guided-decoding support for maxItems is uneven, so treat this as a
    # post-parse cap rather than a generation constraint.
    observations: list[Observation] = Field(default_factory=list, max_length=8)
