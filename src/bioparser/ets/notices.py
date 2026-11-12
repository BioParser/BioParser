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


class OutOfRange(BaseModel):
    """A printed number outside the range of its column."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["out_of_range"] = "out_of_range"
    field: Literal[
        "year",
        "month",
        "day",
        "decimalLongitude",
        "decimalLatitude",
        "individualCount",
        "dispersion",
    ]


class NotNumeric(BaseModel):
    """Text in a numeric column."""

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
        "traitValue",
    ]


class LevelHasSpread(BaseModel):
    """A level also stored a dispersion, a minimum, or a maximum.

    The four standardized columns stay empty. The printed numbers stay.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["level_has_spread"] = "level_has_spread"
    field: Literal["traitName"] = "traitName"


class NotOnList(BaseModel):
    """A trait code was stored and the list has no entry for it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["not_on_list"] = "not_on_list"
    field: Literal["traitName"] = "traitName"


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


class BoundsReversed(BaseModel):
    """The printed minimum is greater than the printed maximum. Both are kept."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["bounds_reversed"] = "bounds_reversed"
    field: Literal["measurementValue_min,measurementValue_max"] = (
        "measurementValue_min,measurementValue_max"
    )


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
    | OutOfRange
    | NotInVocabulary
    | NotADate
    | NotACalendarDay
    | LevelHasSpread
    | NotOnList
    | UnitRefused
    | BoundsReversed
    | DeprecatedTrait
    | Approximate
)


def format_warning(notice: Notice) -> str:
    """The ETS warnings column is one character string. This is one code in it."""
    return f"{notice.kind}:{notice.field}"
