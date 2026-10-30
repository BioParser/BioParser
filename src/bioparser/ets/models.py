"""One ETS v0.10 measurement row.

Dataset metadata, the traitlist, and the Biodiversity Exploratories extension are
not columns of a measurement, so they are not fields here. Evidence is not an ETS
term. The artifact trait code is not a column.
"""

import re
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bioparser.ets.numbers import canonical_decimal
from bioparser.ets.vocab import BasisOfRecord, Sex

_MEASUREMENT_ID = re.compile(r"^[1-9]\d*$")


class EtsRow(BaseModel):
    """ETS v0.10 core, taxon, measurement-or-fact, and occurrence columns.

    The order follows the stored observation. Columns the artifact does not
    store sit beside the field they belong with.
    """

    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    # Both come from the trait list (traits.py), so every row has them.
    traitID: str = Field(min_length=1)
    traitName: str = Field(min_length=1)
    verbatimTraitName: str | None = None

    # Accepted name. taxonID is the optional taxonomy identifier.
    scientificName: str = Field(min_length=1)
    verbatimScientificName: str = Field(min_length=1)
    taxonID: str | None = None

    # Standardized value beside the printed one.
    traitValue: str = Field(min_length=1)
    verbatimTraitValue: str = Field(min_length=1)
    # None for a categorical trait, which has no unit. The printed unit stays in verbatimTraitUnit.
    traitUnit: str | None = Field(default=None, min_length=1)
    verbatimTraitUnit: str = Field(min_length=1)

    # Classification.
    taxonRank: str | None = None
    kingdom: str | None = None
    phylum: str | None = None
    taxonClass: str | None = Field(default=None, alias="class")
    order: str | None = None
    family: str | None = None
    genus: str | None = None

    # The organism.
    sex: Sex | None = None
    lifeStage: str | None = None
    age: float | None = Field(default=None, allow_inf_nan=False)
    morphotype: str | None = None

    # Where the measurement came from.
    basisOfRecord: BasisOfRecord
    basisOfRecordDescription: str | None = None

    # How the value was measured.
    measurementMethod: str | None = None
    measurementResolution: str | None = None
    measurementRemarks: str | None = None
    measurementDeterminedBy: str | None = None
    measurementDeterminedDate: str | None = None

    # Repeated or grouped measurements.
    aggregateMeasure: bool | None = None
    statisticalMethod: str | None = None
    individualCount: int | None = Field(default=None, ge=1)
    dispersion: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    measurementValue_min: float | None = Field(default=None, allow_inf_nan=False)
    measurementValue_max: float | None = Field(default=None, allow_inf_nan=False)
    measurementAccuracy: str | None = None

    # measurementID is assigned for this response.
    measurementID: str
    occurrenceID: str | None = None
    warnings: str
    references: str = Field(min_length=1)

    # Where and when the organism was recorded.
    eventID: str | None = None
    preparations: str | None = None
    samplingProtocol: str | None = None
    year: int | None = None
    month: int | None = None
    day: int | None = None
    eventDate: str | None = None
    locationID: str | None = None
    habitat: str | None = None
    decimalLongitude: float | None = Field(default=None, allow_inf_nan=False)
    decimalLatitude: float | None = Field(default=None, allow_inf_nan=False)
    elevation: float | None = Field(default=None, allow_inf_nan=False)
    geodeticDatum: str | None = None
    verbatimLocality: str | None = None
    country: str | None = None
    countryCode: str | None = None
    occurrenceRemarks: str | None = None

    @model_validator(mode="after")
    def _trait_value_matches_its_kind(self) -> Self:
        # A trait with a unit is a number. A trait without one is a categorical level, and
        # assess has already matched it to the trait's levels.
        if self.traitUnit is not None and canonical_decimal(self.traitValue) != self.traitValue:
            raise ValueError("traitValue is not a positive decimal in canonical form")
        return self

    @field_validator("measurementID")
    @classmethod
    def _measurement_id_is_a_decimal_string(cls, value: str) -> str:
        if _MEASUREMENT_ID.fullmatch(value) is None:
            raise ValueError("measurementID is not a decimal integer string")
        return value

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> Self:
        lower = self.measurementValue_min
        upper = self.measurementValue_max
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("measurementValue_min is greater than measurementValue_max")
        return self
