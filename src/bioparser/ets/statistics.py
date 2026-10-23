"""Read a summarized measurement and convert it into the trait unit.

The observation stores the central value, the dispersion, and the bounds as
closed choices. Those choices select the factor. When no phrase is stored,
they also supply ``statisticalMethod``. The observation is left unchanged.
"""

from dataclasses import dataclass
from decimal import Decimal

from bioparser.ets.notices import (
    BoundMissing,
    KindMissing,
    Notice,
    NotInVocabulary,
    NotNumeric,
    NumberMissing,
    NumericField,
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
class MeasurementSummary:
    """Dispersion and bounds. They stay in the printed unit until scale_summary."""

    dispersion: float | None
    measurement_value_min: float | None
    measurement_value_max: float | None
    phrase: str | None
    dispersion_kind: DispersionKind | None
    bounds_kind: BoundsKind | None


def read_summary(
    observation: Observation,
    *,
    stored_phrase: str | None,
    warnings: list[Notice],
) -> MeasurementSummary:
    """Parse the choices and the numbers. Scaling waits for the trait factor.

    A missing dispersion is 0. A dispersion number without a kind, a kind
    without a number, and a bounds choice missing one side are warnings.
    """
    dispersion = _parse_dispersion(observation.dispersion, warnings)
    minimum = _parse_bound(observation.measurementValueMin, "measurementValue_min", warnings)
    maximum = _parse_bound(observation.measurementValueMax, "measurementValue_max", warnings)
    central = _central(observation.centralKind, warnings)
    dispersion_kind = _dispersion_kind(observation.dispersionKind, warnings)
    bounds_kind = _bounds_kind(observation.boundsKind, warnings)
    _gaps(
        observation,
        dispersion=dispersion,
        dispersion_kind=dispersion_kind,
        bounds_kind=bounds_kind,
        warnings=warnings,
    )
    return MeasurementSummary(
        dispersion=dispersion,
        measurement_value_min=minimum,
        measurement_value_max=maximum,
        phrase=_phrase(stored_phrase, central, dispersion_kind),
        dispersion_kind=dispersion_kind,
        bounds_kind=bounds_kind,
    )


def scale_summary(
    observation: Observation,
    summary: MeasurementSummary,
    factor: Decimal,
) -> MeasurementSummary:
    """The same factor as the trait value. Variance uses the square of that factor."""
    return MeasurementSummary(
        dispersion=_scale_dispersion(
            observation, summary.dispersion, summary.dispersion_kind, factor
        ),
        measurement_value_min=_scale_bound(
            observation.measurementValueMin,
            summary.measurement_value_min,
            summary.bounds_kind,
            factor,
        ),
        measurement_value_max=_scale_bound(
            observation.measurementValueMax,
            summary.measurement_value_max,
            summary.bounds_kind,
            factor,
        ),
        phrase=summary.phrase,
        dispersion_kind=summary.dispersion_kind,
        bounds_kind=summary.bounds_kind,
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
    dispersion_kind: DispersionKind | None,
    bounds_kind: BoundsKind | None,
    warnings: list[Notice],
) -> None:
    number_stored = observation.dispersion is not None and dispersion is not None
    if number_stored and observation.dispersionKind is None:
        warnings.append(KindMissing())
    elif observation.dispersion is None and dispersion_kind is not None:
        warnings.append(NumberMissing())
    if bounds_kind is None:
        return
    if observation.measurementValueMin is None:
        warnings.append(BoundMissing(field="measurementValue_min"))
    if observation.measurementValueMax is None:
        warnings.append(BoundMissing(field="measurementValue_max"))


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


def _central(value: Interpretation | None, warnings: list[Notice]) -> CentralKind | None:
    if value is None:
        return None
    matched = match_central(value.value)
    if matched is None:
        warnings.append(NotInVocabulary(field="centralKind"))
        return None
    return matched


def _dispersion_kind(value: Interpretation | None, warnings: list[Notice]) -> DispersionKind | None:
    if value is None:
        return None
    matched = match_dispersion_kind(value.value)
    if matched is None:
        warnings.append(NotInVocabulary(field="dispersionKind"))
        return None
    return matched


def _bounds_kind(value: Interpretation | None, warnings: list[Notice]) -> BoundsKind | None:
    if value is None:
        return None
    matched = match_bounds(value.value)
    if matched is None:
        warnings.append(NotInVocabulary(field="boundsKind"))
        return None
    return matched


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
