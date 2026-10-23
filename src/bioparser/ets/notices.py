"""Why a stored field did not become the ETS column."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

# Free-text columns copied onto the row. taxonClass is written as class.
TextField = Literal[
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
    "lifeStage",
    "morphotype",
    "basisOfRecordDescription",
    "measurementMethod",
    "measurementResolution",
    "measurementRemarks",
    "measurementDeterminedBy",
    "statisticalMethod",
    "measurementAccuracy",
    "occurrenceID",
    "eventID",
    "preparations",
    "samplingProtocol",
    "locationID",
    "habitat",
    "geodeticDatum",
    "verbatimLocality",
    "country",
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


class NotNumeric(BaseModel):
    """A printed number that is not the ETS numeric type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["not_numeric"] = "not_numeric"
    field: NumericField


class NotInVocabulary(BaseModel):
    """A stored token outside the list for that field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["not_in_vocabulary"] = "not_in_vocabulary"
    field: Literal[
        "sex",
        "basisOfRecord",
        "aggregateMeasure",
        "countryCode",
        "statisticalMethod",
    ]


_SUMMARY_FIELD = Literal["dispersion", "measurementValue_min", "measurementValue_max"]


class NotConverted(BaseModel):
    """A stored number stayed in the printed unit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["not_converted"] = "not_converted"
    field: _SUMMARY_FIELD


class Missing(BaseModel):
    """A declared statistic has no number for this column."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["missing"] = "missing"
    field: _SUMMARY_FIELD


class NotADate(BaseModel):
    """A printed date that is not a calendar day."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["not_a_date"] = "not_a_date"
    field: DateField


class NotACalendarDay(BaseModel):
    """Year, month, and day together are not one calendar day."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["not_a_date"] = "not_a_date"
    field: Literal["year,month,day"] = "year,month,day"


Notice = NotNumeric | NotInVocabulary | NotADate | NotACalendarDay | NotConverted | Missing


def format_warning(notice: Notice) -> str:
    """The ETS warnings column is one character string. This is one code in it."""
    return f"{notice.kind}:{notice.field}"
