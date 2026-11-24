"""Judge one stored observation. The observation is left unchanged."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from bioparser.ets.dates import is_ambiguous_date, parse_date
from bioparser.ets.notices import (
    AmbiguousDate,
    BoundsReversed,
    DateField,
    DateMismatch,
    NotACalendarDay,
    NotADate,
    Notice,
    NotInVocabulary,
    NotNumeric,
    NotVerified,
    NumericField,
    OutOfRange,
    TextField,
)
from bioparser.ets.numbers import parse_decimal, parse_integer, parse_signed_decimal
from bioparser.ets.vocab import BasisOfRecord, match_aggregate, match_basis, match_sex
from bioparser.extract.models import (
    CitationOrCorrection,
    CitationOrInterpretation,
    Correction,
    Interpretation,
    Observation,
    stored_text,
)

# Free-text columns. The value is the ETS name. taxonClass is written as class.
_CITED: dict[str, TextField] = {
    "verbatimScientificName": "verbatimScientificName",
    "verbatimTraitValue": "verbatimTraitValue",
    "verbatimTraitUnit": "verbatimTraitUnit",
    "verbatimTraitName": "verbatimTraitName",
    "kingdom": "kingdom",
    "phylum": "phylum",
    "taxonClass": "class",
    "order": "order",
    "family": "family",
    "genus": "genus",
    "lifeStage": "lifeStage",
    "morphotype": "morphotype",
    "basisOfRecordDescription": "basisOfRecordDescription",
    "measurementMethod": "measurementMethod",
    "measurementResolution": "measurementResolution",
    "measurementRemarks": "measurementRemarks",
    "measurementDeterminedBy": "measurementDeterminedBy",
    "statisticalMethod": "statisticalMethod",
    "measurementAccuracy": "measurementAccuracy",
    "eventID": "eventID",
    "preparations": "preparations",
    "samplingProtocol": "samplingProtocol",
    "locationID": "locationID",
    "habitat": "habitat",
    "geodeticDatum": "geodeticDatum",
    "verbatimLocality": "verbatimLocality",
    "country": "country",
    "occurrenceRemarks": "occurrenceRemarks",
}


class Assessment(BaseModel):
    """The observation becomes a row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    warnings: tuple[Notice, ...]
    scientific_name: str | None
    trait_value: str | None
    trait_unit: str | None
    sex: str | None
    age: str | None
    aggregate_measure: bool | None
    basis_of_record: BasisOfRecord | None
    individual_count: str | None
    dispersion: str | None
    measurement_value_min: str | None
    measurement_value_max: str | None
    year: str | None
    month: str | None
    day: str | None
    event_date: str | None
    measurement_determined_date: str | None
    decimal_longitude: str | None
    decimal_latitude: str | None
    elevation: str | None
    country_code: str | None
    occurrence_id: str | None
    cited: dict[TextField, str | None]


def assess(observation: Observation) -> Assessment:
    """Run the quality rules once. measurementID and references are not decided here."""
    warnings: list[Notice] = []

    # Free-text columns. A correction contributes its interpretation. A citation contributes its text.
    cited = {ets_name: _cited(getattr(observation, name)) for name, ets_name in _CITED.items()}

    # The model's name is the column until a taxonomy hit replaces it. A hit fills taxonID.
    # There is no lookup yet, so a stored name stays and warns not_verified.
    scientific_name = (
        stored_text(observation.scientificName) if observation.scientificName is not None else None
    )
    if scientific_name is not None:
        warnings.append(NotVerified())
    cited["taxonRank"] = None

    # A trait list publishes the standardized value and its unit.
    trait_value = None
    trait_unit = None

    # basisOfRecord and aggregateMeasure stay empty outside their lists.
    # sex keeps the printed word and warns not_in_vocabulary.
    sex = _parse_sex(observation.sex, warnings)
    aggregate_measure = _parse_aggregate(observation.aggregateMeasure, warnings)
    basis_of_record = _parse_basis(observation.basisOfRecord, warnings)

    # Numbers.
    age = _parse_decimal(observation.age, "age", warnings, signed=True)
    individual_count = _parse_individual_count(observation.individualCount, warnings)
    dispersion = _parse_dispersion(observation.dispersion, warnings)
    value_min = _parse_bound(observation.measurementValueMin, "measurementValue_min", warnings)
    value_max = _parse_bound(observation.measurementValueMax, "measurementValue_max", warnings)
    parsed_min = None if value_min is None else parse_signed_decimal(value_min)
    parsed_max = None if value_max is None else parse_signed_decimal(value_max)
    if parsed_min is not None and parsed_max is not None and parsed_min > parsed_max:
        warnings.append(BoundsReversed())

    # A calendar day is written as YYYY-MM-DD. Year, month, and day stay separate.
    year = _parse_integer(observation.year, "year", warnings, low=1000, high=9999)
    month = _parse_integer(observation.month, "month", warnings, low=1, high=12)
    day = _parse_integer(observation.day, "day", warnings, low=1, high=31)
    event_date = _parse_date(observation.eventDate, "eventDate", warnings)

    parsed_event_date = None if event_date is None else parse_date(event_date)
    if parsed_event_date is not None:
        parts = parsed_event_date.split("-")

        if year is None:
            year = parts[0]
        elif parse_integer(year) != int(parts[0]):
            warnings.append(DateMismatch(field="year"))

        if len(parts) >= 2:
            if month is None:
                month = parts[1]
            elif parse_integer(month) != int(parts[1]):
                warnings.append(DateMismatch(field="month"))

        if len(parts) == 3:
            if day is None:
                day = parts[2]
            elif parse_integer(day) != int(parts[2]):
                warnings.append(DateMismatch(field="day"))

    parsed_year = None if year is None else parse_integer(year)
    parsed_month = None if month is None else parse_integer(month)
    parsed_day = None if day is None else parse_integer(day)
    if parsed_year is not None and parsed_month is not None and parsed_day is not None:
        try:
            datetime(parsed_year, parsed_month, parsed_day)
        except ValueError:
            warnings.append(NotACalendarDay())

    measurement_determined_date = _parse_date(
        observation.measurementDeterminedDate, "measurementDeterminedDate", warnings
    )

    # A coordinate outside its range is kept and warns out_of_range.
    decimal_longitude = _parse_coordinate(
        observation.decimalLongitude, "decimalLongitude", warnings, low=-180, high=180
    )
    decimal_latitude = _parse_coordinate(
        observation.decimalLatitude, "decimalLatitude", warnings, low=-90, high=90
    )
    elevation = _parse_decimal(observation.elevation, "elevation", warnings, signed=True)
    country_code = _parse_country_code(observation.countryCode, warnings)
    occurrence_id = (
        None if observation.occurrenceID is None else stored_text(observation.occurrenceID)
    )

    return Assessment(
        warnings=tuple(warnings),
        scientific_name=scientific_name,
        trait_value=trait_value,
        trait_unit=trait_unit,
        sex=sex,
        age=age,
        aggregate_measure=aggregate_measure,
        basis_of_record=basis_of_record,
        individual_count=individual_count,
        dispersion=dispersion,
        measurement_value_min=value_min,
        measurement_value_max=value_max,
        year=year,
        month=month,
        day=day,
        event_date=event_date,
        measurement_determined_date=measurement_determined_date,
        decimal_longitude=decimal_longitude,
        decimal_latitude=decimal_latitude,
        elevation=elevation,
        country_code=country_code,
        occurrence_id=occurrence_id,
        cited=cited,
    )


def _cited(value: CitationOrCorrection | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Correction):
        return value.interpretation.value
    return value.text


def _parse_sex(value: CitationOrCorrection | None, warnings: list[Notice]) -> str | None:
    """Try the reading first, then the printed word. A miss keeps the printed word."""
    if value is None:
        return None
    if isinstance(value, Correction):
        matched = match_sex(value.interpretation.value)
        if matched is not None:
            return matched
        printed = value.citation.text
    else:
        printed = value.text
    matched = match_sex(printed)
    if matched is not None:
        return matched
    warnings.append(NotInVocabulary(field="sex"))
    return printed


def _parse_basis(value: Interpretation | None, warnings: list[Notice]) -> BasisOfRecord | None:
    if value is None:
        return None
    matched = match_basis(value.value)
    if matched is None:
        warnings.append(NotInVocabulary(field="basisOfRecord"))
        return None
    return matched


def _parse_aggregate(value: Interpretation | None, warnings: list[Notice]) -> bool | None:
    if value is None:
        return None
    matched = match_aggregate(value.value)
    if matched is None:
        warnings.append(NotInVocabulary(field="aggregateMeasure"))
        return None
    return matched


def _parse_individual_count(
    value: CitationOrInterpretation | None, warnings: list[Notice]
) -> str | None:
    if value is None:
        return None
    text = stored_text(value)
    parsed = parse_integer(text)
    if parsed is None:
        warnings.append(NotNumeric(field="individualCount"))
        return text
    if parsed < 1:
        warnings.append(OutOfRange(field="individualCount"))
    return text


def _parse_dispersion(value: CitationOrInterpretation | None, warnings: list[Notice]) -> str | None:
    if value is None:
        return None
    text = stored_text(value)
    parsed = parse_signed_decimal(text)
    if parsed is None:
        warnings.append(NotNumeric(field="dispersion"))
        return text
    if parsed < 0:
        warnings.append(OutOfRange(field="dispersion"))
    return text


def _parse_bound(
    value: CitationOrInterpretation | None,
    field: Literal["measurementValue_min", "measurementValue_max"],
    warnings: list[Notice],
) -> str | None:
    if value is None:
        return None
    text = stored_text(value)
    if parse_signed_decimal(text) is None:
        warnings.append(NotNumeric(field=field))
    return text


def _parse_decimal(
    value: CitationOrInterpretation | None,
    field: NumericField,
    warnings: list[Notice],
    *,
    signed: bool,
) -> str | None:
    if value is None:
        return None
    text = stored_text(value)
    parsed = parse_signed_decimal(text) if signed else parse_decimal(text)
    if parsed is None:
        warnings.append(NotNumeric(field=field))
    return text


def _parse_integer(
    value: CitationOrInterpretation | None,
    field: Literal["year", "month", "day"],
    warnings: list[Notice],
    *,
    low: int,
    high: int,
) -> str | None:
    if value is None:
        return None
    text = stored_text(value)
    parsed = parse_integer(text)
    if parsed is None:
        warnings.append(NotNumeric(field=field))
        return text
    if not low <= parsed <= high:
        warnings.append(OutOfRange(field=field))
    return text


def _parse_date(
    value: CitationOrInterpretation | None, field: DateField, warnings: list[Notice]
) -> str | None:
    if value is None:
        return None
    stored = stored_text(value)
    text = parse_date(stored)
    if text is None:
        if is_ambiguous_date(stored):
            warnings.append(AmbiguousDate(field=field))
        else:
            warnings.append(NotADate(field=field))
        return stored
    return text


def _parse_coordinate(
    value: CitationOrInterpretation | None,
    field: Literal["decimalLongitude", "decimalLatitude"],
    warnings: list[Notice],
    *,
    low: float,
    high: float,
) -> str | None:
    text = _parse_decimal(value, field, warnings, signed=True)
    if text is None:
        return None
    parsed = parse_signed_decimal(text)
    if parsed is not None and not low <= parsed <= high:
        warnings.append(OutOfRange(field=field))
    return text


_COUNTRY_CODE = re.compile(r"^[A-Za-z]{2}$")


def _parse_country_code(
    value: CitationOrInterpretation | None, warnings: list[Notice]
) -> str | None:
    if value is None:
        return None
    text = stored_text(value)
    if _COUNTRY_CODE.fullmatch(text) is None:
        warnings.append(NotInVocabulary(field="countryCode"))
        return text
    return text.upper()
