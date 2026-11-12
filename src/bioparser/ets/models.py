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

    # Optional thesaurus identifier.
    traitID: str | None = None
    # A trait list fills this.
    traitName: str | None = None
    verbatimTraitName: str = Field(min_length=1)

    # Accepted name. A taxonomy lookup fills scientificName and taxonID together.
    scientificName: str | None = None
    verbatimScientificName: str = Field(min_length=1)
    taxonID: str | None = None

    # The trait list fills the value and the unit. The verbatim fields are printed.
    traitValue: str | None = None
    verbatimTraitValue: str = Field(min_length=1)
    traitUnit: str | None = None
    verbatimTraitUnit: str | None = None

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
    # No default. ETS says "Defaults to 1" and "Defaults to 0". A missing value is
    # empty, because a default would state something the paper did not say.
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
    def _taxon_fields_are_one_resolution(self) -> Self:
        """A taxonomy lookup fills the accepted name and the identifier together."""
        if (self.scientificName is None) != (self.taxonID is None):
            raise ValueError("scientificName and taxonID are filled together")
        return self

    @model_validator(mode="after")
    def _trait_fields_are_one_standardization(self) -> Self:
        """A trait list fills the name, the identifier, and the value together.

        A unit belongs to that value, and the value is then a number. A level has
        a value and no unit.
        """
        named = (self.traitID, self.traitName, self.traitValue)
        filled = [field is not None for field in named]
        if any(filled) and not all(filled):
            raise ValueError("traitID, traitName, and traitValue are filled together")
        value = self.traitValue
        if self.traitUnit is not None and (value is None or canonical_decimal(value) != value):
            raise ValueError("traitValue is not a positive decimal in the trait unit")
        return self

    @field_validator("measurementID")
    @classmethod
    def _measurement_id_is_a_decimal_string(cls, value: str) -> str:
        if _MEASUREMENT_ID.fullmatch(value) is None:
            raise ValueError("measurementID is not a decimal integer string")
        return value
