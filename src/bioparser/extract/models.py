from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Trait = Literal["body_mass", "body_length", "tail_length"]

EXTRACTION_ARTIFACT_SCHEMA_VERSION: Literal["1"] = "1"


class TextRange(BaseModel):
    """Half-open character range in one parser block.

    ``start`` and ``end`` are Unicode code points, the same indexes as a Python
    ``str``. ``end`` is greater than ``start``.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    block_id: str = Field(min_length=1)
    start: int
    end: int

    @model_validator(mode="after")
    def _ordered(self) -> "TextRange":
        if self.start < 0 or self.end <= self.start:
            raise ValueError("evidence range is empty or out of order")
        return self


class Cited(BaseModel):
    """One verbatim ETS field. Evidence is absent when the text was not located."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    text: str = Field(min_length=1)
    evidence: TextRange | None = None


class Observation(BaseModel):
    """One measurement. Each paper field is grounded on its own."""

    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    trait: Trait
    verbatimScientificName: Cited
    verbatimTraitValue: Cited
    verbatimTraitUnit: Cited
    verbatimTraitName: Cited | None = None
    taxonRank: Cited | None = None
    kingdom: Cited | None = None
    phylum: Cited | None = None
    taxonClass: Cited | None = Field(default=None, alias="class")
    order: Cited | None = None
    family: Cited | None = None
    genus: Cited | None = None
    sex: Cited | None = None
    lifeStage: Cited | None = None
    age: Cited | None = None
    morphotype: Cited | None = None
    basisOfRecord: Cited | None = None
    measurementMethod: Cited | None = None
    measurementResolution: Cited | None = None
    measurementRemarks: Cited | None = None
    aggregateMeasure: Cited | None = None
    statisticalMethod: Cited | None = None
    individualCount: Cited | None = None
    dispersion: Cited | None = None
    measurementValue_min: Cited | None = None
    measurementValue_max: Cited | None = None
    measurementAccuracy: Cited | None = None


class ExtractionArtifact(BaseModel):
    """Versioned extraction result stored for a job."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    schema_version: Literal["1"] = EXTRACTION_ARTIFACT_SCHEMA_VERSION
    extractor: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    observations: list[Observation] = Field(default_factory=list)
