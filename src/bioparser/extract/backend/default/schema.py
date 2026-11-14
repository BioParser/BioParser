from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class ModelObservation(BaseModel):
    """One measurement as the model reports it.

    The length caps are defence in depth behind max_tokens and are deliberately
    generous: exceeding one fails the whole extraction, not just the offending
    observation.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, populate_by_name=True)

    taxon: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=80)
    unit: str = Field(min_length=1, max_length=80)
    evidence_block_id: str = Field(min_length=1, max_length=80)
    quotation: str = Field(min_length=1, max_length=300)
    trait_code: str | None = Field(
        default=None,
        max_length=80,
        validation_alias=AliasChoices("trait_code", "trait"),
    )

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value_str(cls, v: object) -> str:
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, str):
            return v
        raise ValueError(f"expected string or number, got {type(v).__name__}")


class Extraction(BaseModel):
    """Guided-decoding payload for this backend."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    # 8 * ~70 tokens = ~560 tokens; raise --max-model-len before raising this.
    # Guided-decoding support for maxItems is uneven, so treat this as a
    # post-parse cap rather than a generation constraint.
    observations: list[ModelObservation] = Field(default_factory=list, max_length=8)
