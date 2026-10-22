"""Judge one stored observation. The observation is left unchanged."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from bioparser.ets.dates import parse_date
from bioparser.ets.numbers import (
    canonical_decimal,
    parse_count,
    parse_decimal,
    parse_integer,
    parse_signed_decimal,
)
from bioparser.ets.units import CanonicalTraitValue, TargetUnit, convert_trait_value_to_canonical
from bioparser.ets.warnings import (
    DateField,
    NotADate,
    NotInVocabulary,
    NotNumeric,
    NumericField,
    Unlocated,
    UnlocatedField,
    Warning,
)
from bioparser.ets_vocab import BasisOfRecord, Sex, match_sex
from bioparser.extract.models import (
    AggregateInterpretation,
    BasisInterpretation,
    Citation,
    CitationOrInterpretation,
    CitationOrSexInterpretation,
    CorrectableCitation,
    Correction,
    Interpretation,
    Observation,
    SexInterpretation,
)

DropReason = Literal[
    "unit_not_for_trait",
    "unit_unknown",
    "value_not_numeric",
    "bounds_reversed",
]
# Free-text columns. The value is the ETS name. taxonClass is written as class.
_CITED: dict[str, UnlocatedField] = {
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


class _Fields(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    warnings: tuple[Warning, ...]
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
    cited: dict[UnlocatedField, str | None]


class Kept(_Fields):
    """The observation can become a row. The converted value is present."""

    trait_value: str
    trait_unit: TargetUnit


class Dropped(_Fields):
    """The observation cannot become a row."""

    drop: DropReason


Assessment = Kept | Dropped


def assess(observation: Observation) -> Assessment:
    """Run the quality rules once. measurementID and references are not decided here."""
    warnings: list[Warning] = []

    # The trait code is not a column. Only its passage can be unlocated.
    _warn_unlocated(observation.trait.passage, "trait", warnings)

    # Free-text citations. The ETS column is the narrow cited text.
    cited = {
        ets_name: _cited(getattr(observation, name), ets_name, warnings)
        for name, ets_name in _CITED.items()
    }

    # Closed tokens. A missing basis becomes literatureData.
    sex = _sex(observation.sex, warnings)
    aggregate_measure = _aggregate(observation.aggregateMeasure, warnings)
    basis_of_record = _basis(observation.basisOfRecord, warnings)

    # Numbers. ETS defaults a missing count to 1 and a missing dispersion to 0.
    age = _parse_decimal(observation.age, "age", warnings, signed=True)
    individual_count = _parse_individual_count(observation.individualCount, warnings)
    measurement_value_min = _parse_decimal(
        observation.measurementValue_min, "measurementValue_min", warnings, signed=True
    )
    measurement_value_max = _parse_decimal(
        observation.measurementValue_max, "measurementValue_max", warnings, signed=True
    )
    dispersion = _parse_dispersion(observation.dispersion, warnings)

    # A calendar day is written as YYYY-MM-DD. Year, month, and day stay separate.
    year = _parse_integer(observation.year, "year", warnings, low=1000, high=9999)
    month = _parse_integer(observation.month, "month", warnings, low=1, high=12)
    day = _parse_integer(observation.day, "day", warnings, low=1, high=31)
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

    shared = _Fields(
        warnings=tuple(warnings),
        sex=sex,
        age=age,
        aggregate_measure=aggregate_measure,
        basis_of_record=basis_of_record,
        individual_count=individual_count,
        dispersion=dispersion,
        measurement_value_min=measurement_value_min,
        measurement_value_max=measurement_value_max,
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
    parsed = _parse_trait_value(observation)
    if not isinstance(parsed, CanonicalTraitValue):
        return Dropped(drop=parsed, **shared.model_dump())
    if _bounds_reversed(measurement_value_min, measurement_value_max):
        return Dropped(drop="bounds_reversed", **shared.model_dump())
    return Kept(
        trait_value=parsed.value,
        trait_unit=parsed.unit,
        **shared.model_dump(),
    )


def _bounds_reversed(lower: float | None, upper: float | None) -> bool:
    return lower is not None and upper is not None and lower > upper


def _parse_trait_value(observation: Observation) -> CanonicalTraitValue | DropReason:
    stored_value = observation.traitValue
    stored_unit = observation.traitUnit
    if stored_value is not None and stored_unit is not None:
        rendered = canonical_decimal(stored_value.value)
        if rendered is None:
            return "value_not_numeric"
        return CanonicalTraitValue(rendered, stored_unit.value)
    return convert_trait_value_to_canonical(
        observation.trait.value,
        observation.verbatimTraitValue.text,
        observation.verbatimTraitUnit.text,
    )


def _cited(
    value: CorrectableCitation | None, field: UnlocatedField, warnings: list[Warning]
) -> str | None:
    if value is None:
        return None
    citation = value.citation if isinstance(value, Correction) else value
    _warn_unlocated(citation, field, warnings)
    return citation.text


def _sex(value: CitationOrSexInterpretation | None, warnings: list[Warning]) -> Sex | None:
    if value is None:
        return None
    if isinstance(value, SexInterpretation):
        _warn_unlocated(value.passage, "sex", warnings)
        return value.value
    _warn_unlocated(value, "sex", warnings)
    matched = match_sex(value.text)
    if matched is None:
        warnings.append(NotInVocabulary(field="sex"))
    return matched


def _basis(value: BasisInterpretation | None, warnings: list[Warning]) -> BasisOfRecord:
    if value is None:
        return "literatureData"
    _warn_unlocated(value.passage, "basisOfRecord", warnings)
    return value.value


def _aggregate(value: AggregateInterpretation | None, warnings: list[Warning]) -> bool | None:
    if value is None:
        return None
    _warn_unlocated(value.passage, "aggregateMeasure", warnings)
    return value.value == "TRUE"


def _parse_individual_count(
    value: CitationOrInterpretation | None, warnings: list[Warning]
) -> int | None:
    if value is None:
        return 1
    parsed = parse_count(_reading(value, "individualCount", warnings))
    if parsed is None:
        warnings.append(NotNumeric(field="individualCount"))
        return None
    return parsed


def _parse_dispersion(
    value: CitationOrInterpretation | None, warnings: list[Warning]
) -> float | None:
    if value is None:
        return 0.0
    parsed = parse_decimal(_reading(value, "dispersion", warnings))
    if parsed is None:
        warnings.append(NotNumeric(field="dispersion"))
        return None
    return parsed


def _parse_decimal(
    value: CitationOrInterpretation | None,
    field: NumericField,
    warnings: list[Warning],
    *,
    signed: bool,
) -> float | None:
    if value is None:
        return None
    text = _reading(value, field, warnings)
    parsed = parse_signed_decimal(text) if signed else parse_decimal(text)
    if parsed is None:
        warnings.append(NotNumeric(field=field))
        return None
    return parsed


def _parse_integer(
    value: CitationOrInterpretation | None,
    field: NumericField,
    warnings: list[Warning],
    *,
    low: int,
    high: int,
) -> int | None:
    if value is None:
        return None
    parsed = parse_integer(_reading(value, field, warnings))
    if parsed is None or not low <= parsed <= high:
        warnings.append(NotNumeric(field=field))
        return None
    return parsed


def _parse_date(
    value: CitationOrInterpretation | None, field: DateField, warnings: list[Warning]
) -> str | None:
    if value is None:
        return None
    text = parse_date(_reading(value, field, warnings))
    if text is None:
        warnings.append(NotADate(field=field))
        return None
    return text


def _parse_coordinate(
    value: CitationOrInterpretation | None,
    field: NumericField,
    warnings: list[Warning],
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
    value: CitationOrInterpretation | None, warnings: list[Warning]
) -> str | None:
    if value is None:
        return None
    text = _reading(value, "countryCode", warnings)
    if _COUNTRY_CODE.fullmatch(text) is None:
        warnings.append(NotInVocabulary(field="countryCode"))
        return None
    return text


def _reading(
    value: Citation | Interpretation, field: UnlocatedField, warnings: list[Warning]
) -> str:
    if isinstance(value, Interpretation):
        _warn_unlocated(value.passage, field, warnings)
        return value.value
    _warn_unlocated(value, field, warnings)
    return value.text


def _warn_unlocated(citation: Citation, field: UnlocatedField, warnings: list[Warning]) -> None:
    if citation.evidence is None:
        warnings.append(Unlocated(field=field))
