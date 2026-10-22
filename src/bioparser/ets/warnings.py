"""Why a stored field did not become the ETS column."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

UnlocatedField = Literal[
    "trait",
    "traitValue",
    "traitUnit",
    "verbatimScientificName",
    "verbatimTraitValue",
    "verbatimTraitUnit",
    "verbatimTraitName",
    "taxonRank",
    "kingdom",
    "phylum",
    "class",
    "order",
    "family",
    "genus",
    "sex",
    "lifeStage",
    "age",
    "morphotype",
    "basisOfRecord",
    "basisOfRecordDescription",
    "measurementMethod",
    "measurementResolution",
    "measurementRemarks",
    "measurementDeterminedBy",
    "measurementDeterminedDate",
    "aggregateMeasure",
    "statisticalMethod",
    "individualCount",
    "dispersion",
    "measurementValue_min",
    "measurementValue_max",
    "measurementAccuracy",
    "occurrenceID",
    "eventID",
    "preparations",
    "samplingProtocol",
    "year",
    "month",
    "day",
    "eventDate",
    "locationID",
    "habitat",
    "decimalLongitude",
    "decimalLatitude",
    "elevation",
    "geodeticDatum",
    "verbatimLocality",
    "country",
    "countryCode",
    "occurrenceRemarks",
]
NumericField = Literal[
    "individualCount",
    "age",
    "measurementValue_min",
    "measurementValue_max",
    "dispersion",
    "year",
    "month",
    "day",
    "decimalLongitude",
    "decimalLatitude",
    "elevation",
]
DateField = Literal["measurementDeterminedDate", "eventDate"]


class Unlocated(BaseModel):
    """A stored field whose printed text has no range."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["unlocated"] = "unlocated"
    field: UnlocatedField


class NotNumeric(BaseModel):
    """A printed number that is not the ETS numeric type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["not_numeric"] = "not_numeric"
    field: NumericField


class NotInVocabulary(BaseModel):
    """A printed token outside the ETS list for that column."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["not_in_vocabulary"] = "not_in_vocabulary"
    field: Literal["sex", "basisOfRecord", "countryCode"]


class NotLogical(BaseModel):
    """aggregateMeasure was printed as something other than TRUE or FALSE."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["not_logical"] = "not_logical"
    field: Literal["aggregateMeasure"] = "aggregateMeasure"


class NotADate(BaseModel):
    """A printed date that is not a calendar day."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["not_a_date"] = "not_a_date"
    field: DateField


Warning = Unlocated | NotNumeric | NotInVocabulary | NotLogical | NotADate


def format_warning(warning: Warning) -> str:
    """The ETS warnings column is one character string. This is one code in it."""
    return f"{warning.kind}:{warning.field}"
