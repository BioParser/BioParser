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


class NotConverted(BaseModel):
    """A dispersion, minimum, or maximum stayed in the printed unit.

    Read it with ``verbatimTraitUnit``. The trait value may already be in the stored unit.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["not_converted"] = "not_converted"
    field: Literal["dispersion", "measurementValue_min", "measurementValue_max"]


class NotApplicable(BaseModel):
    """A level stored a spread or a range, so the code was not applied.

    The printed number stays. ``traitName``, ``traitID``, ``traitValue``, and ``traitUnit``
    stay empty.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["not_applicable"] = "not_applicable"
    field: Literal["dispersion", "measurementValue_min", "measurementValue_max"]


class UnmappedTrait(BaseModel):
    """No usable trait code was stored, so the list did not name this measurement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["unmapped"] = "unmapped"
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
    | NotConverted
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
