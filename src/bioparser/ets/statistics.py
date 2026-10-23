"""Read a summarized measurement and convert it into the trait unit.

The observation stores the central value, the dispersion, and the bounds as
closed choices. Those choices select the factor. When no phrase is stored,
they also supply ``statisticalMethod``. The observation is left unchanged.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from bioparser.ets.notices import (
    NotConverted,
    Notice,
    NotInVocabulary,
    NotNumeric,
    NumericField,
    Unspecified,
)
from bioparser.ets.numbers import parse_decimal, parse_signed_decimal
from bioparser.ets.units import scale_measure
from bioparser.ets.vocab import (
    BoundsKind,
    CentralKind,
    DispersionKind,
    match_bounds,
    match_central,
    match_dispersion_kind,
)
from bioparser.extract.models import Citation, CitationOrInterpretation, Interpretation, Observation

_PHRASE: dict[str, str] = {
    "mean": "mean",
    "median": "median",
    "standardDeviation": "standard deviation",
    "variance": "variance",
    "standardError": "standard error",
}


@dataclass(frozen=True)
class MeasurementStatistics:
    """Dispersion and bounds. They stay in the printed unit until scale_statistics."""

    dispersion: float | None
    measurement_value_min: float | None
    measurement_value_max: float | None
    phrase: str | None
    dispersion_kind: DispersionKind | None
    bounds_kind: BoundsKind | None


def read_statistics(
    observation: Observation,
    *,
    stored_phrase: str | None,
    warnings: list[Notice],
) -> MeasurementStatistics:
    """Parse the choices and the numbers. Scaling waits for the trait factor.

    A missing dispersion is 0. A number left in the printed unit, a declared
    statistic with no number, and an unrecognized central choice that leaves no
    phrase are warnings on the ETS column a reader can see.
    """
    dispersion = _parse_dispersion(observation.dispersion, warnings)
    minimum = _parse_bound(observation.measurementValueMin, "measurementValue_min", warnings)
    maximum = _parse_bound(observation.measurementValueMax, "measurementValue_max", warnings)
    central = _central(observation.centralKind)
    dispersion_kind = _dispersion_kind(observation.dispersionKind)
    bounds_kind = _bounds_kind(observation.boundsKind)
    phrase = _phrase(stored_phrase, central, dispersion_kind)
    if phrase is None and observation.centralKind is not None and central is None:
        warnings.append(NotInVocabulary(field="statisticalMethod"))
    _gaps(
        observation,
        dispersion=dispersion,
        minimum=minimum,
        maximum=maximum,
        dispersion_kind=dispersion_kind,
        bounds_kind=bounds_kind,
        warnings=warnings,
    )
    return MeasurementStatistics(
        dispersion=dispersion,
        measurement_value_min=minimum,
        measurement_value_max=maximum,
        phrase=phrase,
        dispersion_kind=dispersion_kind,
        bounds_kind=bounds_kind,
    )


def scale_statistics(
    observation: Observation,
    statistics: MeasurementStatistics,
    factor: Decimal,
) -> MeasurementStatistics:
    """The same factor as the trait value. Variance uses the square of that factor."""
    return MeasurementStatistics(
        dispersion=_scale_dispersion(
            observation, statistics.dispersion, statistics.dispersion_kind, factor
        ),
        measurement_value_min=_scale_bound(
            observation.measurementValueMin,
            statistics.measurement_value_min,
            statistics.bounds_kind,
            factor,
        ),
        measurement_value_max=_scale_bound(
            observation.measurementValueMax,
            statistics.measurement_value_max,
            statistics.bounds_kind,
            factor,
        ),
        phrase=statistics.phrase,
        dispersion_kind=statistics.dispersion_kind,
        bounds_kind=statistics.bounds_kind,
    )


def _phrase(
    stored: str | None, central: CentralKind | None, dispersion: DispersionKind | None
) -> str | None:
    """The printed phrase, or one written from the central and dispersion choices.

    A bounds choice is not part of the phrase. The minimum and the maximum are
    their own columns. ``mean``, ``standardDeviation``, and ``range`` become
    ``mean and standard deviation``.
    """
    if stored is not None:
        return stored
    parts: list[str] = []
    if central is not None:
        parts.append(_PHRASE[central])
    if dispersion is not None:
        parts.append(_PHRASE[dispersion])
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    if len(parts) == 1:
        return parts[0]
    return None


def _gaps(
    observation: Observation,
    *,
    dispersion: float | None,
    minimum: float | None,
    maximum: float | None,
    dispersion_kind: DispersionKind | None,
    bounds_kind: BoundsKind | None,
    warnings: list[Notice],
) -> None:
    choice_stored = observation.dispersionKind is not None
    number_stored = observation.dispersion is not None and dispersion is not None
    if number_stored and dispersion_kind is None:
        warnings.append(NotConverted(field="dispersion"))
    elif choice_stored and observation.dispersion is None:
        warnings.append(Unspecified(field="dispersion"))
    _bound_gap(
        observation.measurementValueMin,
        minimum,
        observation.boundsKind,
        bounds_kind,
        "measurementValue_min",
        warnings,
    )
    _bound_gap(
        observation.measurementValueMax,
        maximum,
        observation.boundsKind,
        bounds_kind,
        "measurementValue_max",
        warnings,
    )


def _bound_gap(
    stored: CitationOrInterpretation | None,
    parsed: float | None,
    choice: Interpretation | None,
    kind: BoundsKind | None,
    field: Literal["measurementValue_min", "measurementValue_max"],
    warnings: list[Notice],
) -> None:
    if stored is None:
        if choice is not None:
            warnings.append(Unspecified(field=field))
        return
    if kind is None and parsed is not None:
        warnings.append(NotConverted(field=field))


def _scale_dispersion(
    observation: Observation,
    value: float | None,
    kind: DispersionKind | None,
    factor: Decimal,
) -> float | None:
    if value is None or kind is None or observation.dispersion is None:
        return value
    return scale_measure(_text(observation.dispersion), factor, squared=kind == "variance")


def _scale_bound(
    stored: CitationOrInterpretation | None,
    value: float | None,
    kind: BoundsKind | None,
    factor: Decimal,
) -> float | None:
    if value is None or kind is None or stored is None:
        return value
    return scale_measure(_text(stored), factor, squared=False)


def _central(value: Interpretation | None) -> CentralKind | None:
    if value is None:
        return None
    return match_central(value.value)


def _dispersion_kind(value: Interpretation | None) -> DispersionKind | None:
    if value is None:
        return None
    return match_dispersion_kind(value.value)


def _bounds_kind(value: Interpretation | None) -> BoundsKind | None:
    if value is None:
        return None
    return match_bounds(value.value)


def _parse_dispersion(
    value: CitationOrInterpretation | None, warnings: list[Notice]
) -> float | None:
    if value is None:
        return 0.0
    parsed = parse_decimal(_text(value))
    if parsed is None:
        warnings.append(NotNumeric(field="dispersion"))
        return None
    return parsed


def _parse_bound(
    value: CitationOrInterpretation | None,
    field: NumericField,
    warnings: list[Notice],
) -> float | None:
    if value is None:
        return None
    parsed = parse_signed_decimal(_text(value))
    if parsed is None:
        warnings.append(NotNumeric(field=field))
        return None
    return parsed


def _text(value: Citation | Interpretation) -> str:
    if isinstance(value, Interpretation):
        return value.value
    return value.text
