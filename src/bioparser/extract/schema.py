from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Trait = Literal["body_mass", "body_length", "tail_length"]

# A closed set, so guided decoding cannot emit "grams", "g." or "gramme". Free
# text here makes values incomparable and there is no way to repair it later.
Unit = Literal["g", "kg", "mm", "cm", "m"]

# Which units are meaningful for which trait. Checked after parsing rather than
# inside the model: a mismatched pair should cost one observation, not the whole
# payload. See bioparser.extract.backend.default.extractor.extract_observations.
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


# hide_input_in_errors: a ValidationError must not quote its input (paper text or
# model output); pipeline failures log their traceback.
class Observation(BaseModel):
    """One measurement as the model reports it.

    This is the guided-decoding schema, so every field is something the model
    must emit. The length caps are defence in depth behind max_tokens and are
    deliberately generous: exceeding one fails the whole extraction, not just
    the offending observation.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    taxon: str = Field(min_length=1, max_length=120)
    trait: Trait
    # allow_inf_nan: json.loads accepts Infinity and NaN, and neither is a mass.
    value: float = Field(gt=0, allow_inf_nan=False)
    unit: Unit
    evidence_block_id: str = Field(min_length=1, max_length=80)
    quotation: str = Field(min_length=1, max_length=300)


EXTRACTION_ARTIFACT_SCHEMA_VERSION: Literal["1"] = "1"


class StoredObservation(BaseModel):
    """One checked measurement as it is stored. The model schema stays `Observation`."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    taxon: str = Field(min_length=1, max_length=120)
    trait: Trait
    value: float = Field(gt=0, allow_inf_nan=False)
    unit: Unit
    evidence_block_id: str = Field(min_length=1, max_length=80)
    quotation: str = Field(min_length=1, max_length=300)
    canonical_value: float = Field(gt=0, allow_inf_nan=False)
    grounded: bool
    unit_ok: bool


class ExtractionArtifact(BaseModel):
    """Versioned extraction result stored for a job."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    schema_version: Literal["1"] = EXTRACTION_ARTIFACT_SCHEMA_VERSION
    observations: list[StoredObservation] = Field(default_factory=list, max_length=8)
    ungrounded: int = Field(ge=0)
    unit_mismatched: int = Field(ge=0)
    blocks_used: int = Field(ge=0)
    prompt_characters: int = Field(ge=0)
    truncated: bool


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    # 8 * ~70 tokens = ~560 tokens; raise --max-model-len before raising this.
    # Guided-decoding support for maxItems is uneven, so treat this as a
    # post-parse cap rather than a generation constraint.
    observations: list[Observation] = Field(default_factory=list, max_length=8)
