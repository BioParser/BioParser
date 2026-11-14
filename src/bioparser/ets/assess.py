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
    sex: str | None
    age: float | str | None
    aggregate_measure: bool | None
    basis_of_record: BasisOfRecord | None
    individual_count: int | str | None
    dispersion: float | str | None
    measurement_value_min: float | str | None
    measurement_value_max: float | str | None
    year: int | str | None
    month: int | str | None
    day: int | str | None
    event_date: str | None
    measurement_determined_date: str | None
    decimal_longitude: float | str | None
    decimal_latitude: float | str | None
    elevation: float | str | None
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

    # Closed tokens. A missing basis stays empty. A token outside the set stays empty.
    sex = _parse_sex(observation.sex, warnings)
    aggregate_measure = _parse_aggregate(observation.aggregateMeasure, warnings)
    basis_of_record = _parse_basis(observation.basisOfRecord, warnings)

    # Numbers.
    age = _parse_decimal(observation.age, "age", warnings, signed=True)
    individual_count = _parse_individual_count(observation.individualCount, warnings)
    dispersion = _parse_dispersion(observation.dispersion, warnings)
    value_min = _parse_bound(observation.measurementValueMin, "measurementValue_min", warnings)
    value_max = _parse_bound(observation.measurementValueMax, "measurementValue_max", warnings)
    if isinstance(value_min, float) and isinstance(value_max, float) and value_min > value_max:
        warnings.append(BoundsReversed())

    # A calendar day is written as YYYY-MM-DD. Year, month, and day stay separate.
    year = _parse_integer(observation.year, "year", warnings, low=1000, high=9999)
    month = _parse_integer(observation.month, "month", warnings, low=1, high=12)
    day = _parse_integer(observation.day, "day", warnings, low=1, high=31)
    if isinstance(year, int) and isinstance(month, int) and isinstance(day, int):
        try:
            datetime(year, month, day)
        except ValueError:
            warnings.append(NotACalendarDay())
    event_date = _parse_date(observation.eventDate, "eventDate", warnings)
    measurement_determined_date = _parse_date(
        observation.measurementDeterminedDate, "measurementDeterminedDate", warnings
    )

    # A coordinate outside the valid range stays empty.
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


def _parse_sex(value: CitationOrInterpretation | None, warnings: list[Notice]) -> str | None:
    if value is None:
        return None
    text = stored_text(value)
    matched = match_sex(text)
    if matched is None:
        warnings.append(NotInVocabulary(field="sex"))
        return text
    return matched


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
) -> int | str | None:
    if value is None:
        return None
    text = stored_text(value)
    parsed = parse_integer(text)
    if parsed is None:
        warnings.append(NotNumeric(field="individualCount"))
        return text
    if parsed < 1:
        warnings.append(OutOfRange(field="individualCount"))
    return parsed


def _parse_dispersion(
    value: CitationOrInterpretation | None, warnings: list[Notice]
) -> float | str | None:
    if value is None:
        return None
    text = stored_text(value)
    parsed = parse_signed_decimal(text)
    if parsed is None:
        warnings.append(NotNumeric(field="dispersion"))
        return text
    if parsed < 0:
        warnings.append(OutOfRange(field="dispersion"))
    return parsed


def _parse_bound(
    value: CitationOrInterpretation | None,
    field: Literal["measurementValue_min", "measurementValue_max"],
    warnings: list[Notice],
) -> float | str | None:
    if value is None:
        return None
    text = stored_text(value)
    parsed = parse_signed_decimal(text)
    if parsed is None:
        warnings.append(NotNumeric(field=field))
        return text
    return parsed


def _parse_decimal(
    value: CitationOrInterpretation | None,
    field: NumericField,
    warnings: list[Notice],
    *,
    signed: bool,
) -> float | str | None:
    if value is None:
        return None
    text = stored_text(value)
    parsed = parse_signed_decimal(text) if signed else parse_decimal(text)
    if parsed is None:
        warnings.append(NotNumeric(field=field))
        return text
    return parsed


def _parse_integer(
    value: CitationOrInterpretation | None,
    field: Literal["year", "month", "day"],
    warnings: list[Notice],
    *,
    low: int,
    high: int,
) -> int | str | None:
    if value is None:
        return None
    text = stored_text(value)
    parsed = parse_integer(text)
    if parsed is None:
        warnings.append(NotNumeric(field=field))
        return text
    if not low <= parsed <= high:
        warnings.append(OutOfRange(field=field))
    return parsed


def _parse_date(
    value: CitationOrInterpretation | None, field: DateField, warnings: list[Notice]
) -> str | None:
    if value is None:
        return None
    stored = stored_text(value)
    text = parse_date(stored)
    if text is None:
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
) -> float | str | None:
    parsed = _parse_decimal(value, field, warnings, signed=True)
    if isinstance(parsed, str) or parsed is None:
        return parsed
    if not low <= parsed <= high:
        warnings.append(OutOfRange(field=field))
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
    return text
