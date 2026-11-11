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
    "traitValue",
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
        "traitValue",
    ]


class NotConverted(BaseModel):
    """A dispersion, minimum, or maximum stayed in the printed unit. Read it with verbatimTraitUnit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["not_converted"] = "not_converted"
    field: Literal["dispersion", "measurementValue_min", "measurementValue_max"] = "dispersion"


class Unspecified(BaseModel):
    """A dispersion choice was stored and there is no number. The column is then 0."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["unspecified"] = "unspecified"
    field: Literal["dispersion"] = "dispersion"


class NotApplicable(BaseModel):
    """A stored number does not apply to this kind of trait, such as a dispersion of a level."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["not_applicable"] = "not_applicable"
    field: Literal[
        "dispersion",
        "measurementValue_min",
        "measurementValue_max",
        "centralKind",
        "dispersionKind",
    ]


class UnmappedTrait(BaseModel):
    """No trait code was stored, so the list did not name this measurement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["unmapped"] = "unmapped"
    field: Literal["traitName"] = "traitName"


class BoundsReversed(BaseModel):
    """The minimum is greater than the maximum, so neither is published."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["bounds_reversed"] = "bounds_reversed"
    field: Literal["measurementValue_min,measurementValue_max"] = (
        "measurementValue_min,measurementValue_max"
    )


class UnitRefused(BaseModel):
    """The trait list could not convert the printed unit, so the value stays as printed.

    ``unit_unknown`` is a unit no numeric trait accepts. ``unit_not_for_trait`` is a known
    unit that this trait does not accept.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["unit_unknown", "unit_not_for_trait"]
    field: Literal["traitUnit"] = "traitUnit"


class DeprecatedTrait(BaseModel):
    """The trait code is deprecated. It still converts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["deprecated_trait"] = "deprecated_trait"
    field: Literal["traitName"] = "traitName"


class Approximate(BaseModel):
    """The value was converted with a conventional factor, such as 365.25 days per year."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["approximate"] = "approximate"
    field: Literal["traitValue"] = "traitValue"


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


Notice = (
    NotNumeric
    | NotInVocabulary
    | NotADate
    | NotACalendarDay
    | NotConverted
    | Unspecified
    | NotApplicable
    | UnmappedTrait
    | UnitRefused
    | BoundsReversed
    | DeprecatedTrait
    | Approximate
)


def format_warning(notice: Notice) -> str:
    """The ETS warnings column is one character string. This is one code in it."""
    return f"{notice.kind}:{notice.field}"
