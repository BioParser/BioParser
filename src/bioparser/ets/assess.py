"""Judge one stored observation. The observation is left unchanged."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from bioparser.ets.dates import parse_date
from bioparser.ets.notices import (
    BoundsReversed,
    DateField,
    NotACalendarDay,
    NotADate,
    Notice,
    NotInVocabulary,
    NotNumeric,
    NumericField,
    TextField,
)
from bioparser.ets.numbers import parse_count, parse_decimal, parse_integer, parse_signed_decimal
from bioparser.ets.vocab import BasisOfRecord, Sex, match_aggregate, match_basis, match_sex
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
    "taxonRank": "taxonRank",
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
    "occurrenceID": "occurrenceID",
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
    sex: Sex | None
    age: float | None
    aggregate_measure: bool | None
    basis_of_record: BasisOfRecord
    individual_count: int | None
    dispersion: float | None
    measurement_value_min: float | None
    measurement_value_max: float | None
    year: int | None
    month: int | None
    day: int | None
    event_date: str | None
    measurement_determined_date: str | None
    decimal_longitude: float | None
    decimal_latitude: float | None
    elevation: float | None
    country_code: str | None
    cited: dict[TextField, str | None]


def assess(observation: Observation) -> Assessment:
    """Run the quality rules once. measurementID and references are not decided here."""
    warnings: list[Notice] = []

    # Free-text columns. A correction contributes its interpretation. A citation contributes its text.
    cited = {ets_name: _cited(getattr(observation, name)) for name, ets_name in _CITED.items()}
    # A taxonomy lookup publishes the accepted name.
    scientific_name = None
    # A trait list publishes the standardized value and its unit.
    trait_value = None
    trait_unit = None

    # Closed tokens. A missing or unmatched basis becomes literatureData.
    sex = _parse_sex(observation.sex, warnings)
    aggregate_measure = _parse_aggregate(observation.aggregateMeasure, warnings)
    basis_of_record = _parse_basis(observation.basisOfRecord, warnings)

    # Numbers.
    age = _parse_decimal(observation.age, "age", warnings, signed=True)
    individual_count = _parse_individual_count(observation.individualCount, warnings)
    dispersion = _parse_dispersion(observation.dispersion, warnings)
    value_min = _parse_bound(observation.measurementValueMin, "measurementValue_min", warnings)
    value_max = _parse_bound(observation.measurementValueMax, "measurementValue_max", warnings)
    if value_min is not None and value_max is not None and value_min > value_max:
        warnings.append(BoundsReversed())

    # A calendar day is written as YYYY-MM-DD. Year, month, and day stay separate.
    year = _parse_integer(observation.year, "year", warnings, low=1000, high=9999)
    month = _parse_integer(observation.month, "month", warnings, low=1, high=12)
    day = _parse_integer(observation.day, "day", warnings, low=1, high=31)
    if year is not None and month is not None and day is not None:
        try:
            datetime(year, month, day)
        except ValueError:
            warnings.append(NotACalendarDay())
            day = None
    event_date = _parse_date(observation.eventDate, "eventDate", warnings)
    measurement_determined_date = _parse_date(
        observation.measurementDeterminedDate, "measurementDeterminedDate", warnings
    )

    # Coordinates outside the valid range stay empty.
    decimal_longitude = _parse_coordinate(
        observation.decimalLongitude, "decimalLongitude", warnings, low=-180, high=180
    )
    decimal_latitude = _parse_coordinate(
        observation.decimalLatitude, "decimalLatitude", warnings, low=-90, high=90
    )
    elevation = _parse_decimal(observation.elevation, "elevation", warnings, signed=True)
    country_code = _parse_country_code(observation.countryCode, warnings)

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
        cited=cited,
    )


def _cited(value: CitationOrCorrection | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Correction):
        return value.interpretation.value
    return value.text


def _parse_sex(value: CitationOrInterpretation | None, warnings: list[Notice]) -> Sex | None:
    if value is None:
        return None
    matched = match_sex(stored_text(value))
    if matched is None:
        warnings.append(NotInVocabulary(field="sex"))
    return matched


def _parse_basis(value: Interpretation | None, warnings: list[Notice]) -> BasisOfRecord:
    if value is None:
        return "literatureData"
    matched = match_basis(value.value)
    if matched is None:
        warnings.append(NotInVocabulary(field="basisOfRecord"))
        return "literatureData"
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
) -> int | None:
    if value is None:
        return None
    parsed = parse_count(stored_text(value))
    if parsed is None:
        warnings.append(NotNumeric(field="individualCount"))
        return None
    return parsed


def _parse_dispersion(
    value: CitationOrInterpretation | None, warnings: list[Notice]
) -> float | None:
    if value is None:
        return None
    parsed = parse_decimal(stored_text(value))
    if parsed is None:
        warnings.append(NotNumeric(field="dispersion"))
        return None
    return parsed


def _parse_bound(
    value: CitationOrInterpretation | None,
    field: Literal["measurementValue_min", "measurementValue_max"],
    warnings: list[Notice],
) -> float | None:
    if value is None:
        return None
    parsed = parse_signed_decimal(stored_text(value))
    if parsed is None:
        warnings.append(NotNumeric(field=field))
        return None
    return parsed


def _parse_decimal(
    value: CitationOrInterpretation | None,
    field: NumericField,
    warnings: list[Notice],
    *,
    signed: bool,
) -> float | None:
    if value is None:
        return None
    text = stored_text(value)
    parsed = parse_signed_decimal(text) if signed else parse_decimal(text)
    if parsed is None:
        warnings.append(NotNumeric(field=field))
        return None
    return parsed


def _parse_integer(
    value: CitationOrInterpretation | None,
    field: NumericField,
    warnings: list[Notice],
    *,
    low: int,
    high: int,
) -> int | None:
    if value is None:
        return None
    parsed = parse_integer(stored_text(value))
    if parsed is None or not low <= parsed <= high:
        warnings.append(NotNumeric(field=field))
        return None
    return parsed


def _parse_date(
    value: CitationOrInterpretation | None, field: DateField, warnings: list[Notice]
) -> str | None:
    if value is None:
        return None
    text = parse_date(stored_text(value))
    if text is None:
        warnings.append(NotADate(field=field))
        return None
    return text


def _parse_coordinate(
    value: CitationOrInterpretation | None,
    field: NumericField,
    warnings: list[Notice],
    *,
    low: float,
    high: float,
) -> float | None:
    parsed = _parse_decimal(value, field, warnings, signed=True)
    if parsed is None:
        return None
    if not low <= parsed <= high:
        warnings.append(NotNumeric(field=field))
        return None
    return parsed


_COUNTRY_CODE = re.compile(r"^[A-Z]{2}$")


def _parse_country_code(
    value: CitationOrInterpretation | None, warnings: list[Notice]
) -> str | None:
    if value is None:
        return None
    text = stored_text(value)
    if _COUNTRY_CODE.fullmatch(text) is None:
        warnings.append(NotInVocabulary(field="countryCode"))
        return None
    return text
