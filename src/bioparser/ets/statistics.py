"""Read a summarized measurement and convert it into the trait unit.

The observation stores the central value and the dispersion as closed choices.
The dispersion choice selects the factor. When no phrase is stored, the choices
also supply ``statisticalMethod``. The minimum and the maximum use the same
factor as the trait value. The observation is left unchanged.
"""

from dataclasses import dataclass
from decimal import Decimal

from bioparser.ets.notices import (
    NotConverted,
    Notice,
    NotInVocabulary,
    NotNumeric,
    NumericField,
    Unspecified,
)
from bioparser.ets.numbers import parse_decimal, parse_signed_decimal
from bioparser.ets.trait_values import scale_measure
from bioparser.ets.vocab import CentralKind, DispersionKind, match_central, match_dispersion_kind
from bioparser.extract.models import (
    CitationOrInterpretation,
    Interpretation,
    Observation,
    stored_text,
)

_PHRASE: dict[str, str] = {
    "mean": "mean",
    "median": "median",
    "standardDeviation": "standard deviation",
    "variance": "variance",
    "standardError": "standard error",
}


@dataclass(frozen=True)
class MeasurementStatistics:
    """Dispersion and bounds, in the trait unit when a factor was available."""

    dispersion: float | None
    measurement_value_min: float | None
    measurement_value_max: float | None
    phrase: str | None


def read_statistics(
    observation: Observation,
    *,
    trait_factor: Decimal | None,
    stored_phrase: str | None,
    warnings: list[Notice],
) -> MeasurementStatistics:
    """Parse the choices and the numbers, then convert with the trait factor.

    A missing dispersion is 0. A dispersion number left in the printed unit, a
    dispersion choice with no number, and an unrecognized central choice are
    warnings on the ETS column a reader can see.
    """
    dispersion = _parse_dispersion(observation.dispersion, warnings)
    minimum = _parse_bound(observation.measurementValueMin, "measurementValue_min", warnings)
    maximum = _parse_bound(observation.measurementValueMax, "measurementValue_max", warnings)
    central = _central(observation.centralKind)
    if observation.centralKind is not None and central is None:
        warnings.append(NotInVocabulary(field="statisticalMethod"))
    dispersion_kind = _dispersion_kind(observation.dispersionKind)
    # A missing dispersion parses as 0, so the stored field decides whether the phrase names it.
    named = dispersion_kind if observation.dispersion is not None else None
    phrase = _phrase(stored_phrase, central, named)
    dispersion = _dispersion_value(observation, dispersion, dispersion_kind, trait_factor, warnings)
    return MeasurementStatistics(
        dispersion=dispersion,
        measurement_value_min=_scale_bound(observation.measurementValueMin, minimum, trait_factor),
        measurement_value_max=_scale_bound(observation.measurementValueMax, maximum, trait_factor),
        phrase=phrase,
    )


def _phrase(
    stored: str | None, central: CentralKind | None, dispersion: DispersionKind | None
) -> str | None:
    """The printed phrase, or one written from the central and dispersion choices.

    The dispersion name is included only when a dispersion number was stored.
    The minimum and the maximum are their own columns. ``mean`` and
    ``standardDeviation`` become ``mean and standard deviation``.
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


def _dispersion_value(
    observation: Observation,
    parsed: float | None,
    kind: DispersionKind | None,
    factor: Decimal | None,
    warnings: list[Notice],
) -> float | None:
    number_stored = observation.dispersion is not None and parsed is not None
    if number_stored and kind is None:
        if factor is not None and factor != 1:
            warnings.append(NotConverted())
        return parsed
    if observation.dispersionKind is not None and observation.dispersion is None:
        warnings.append(Unspecified())
        return parsed
    if parsed is None or observation.dispersion is None or factor is None or kind is None:
        return parsed
    return _scale(observation.dispersion, parsed, factor, squared=kind == "variance")


def _scale_bound(
    stored: CitationOrInterpretation | None,
    parsed: float | None,
    factor: Decimal | None,
) -> float | None:
    if stored is None or parsed is None:
        return parsed
    return _scale(stored, parsed, factor, squared=False)


def _scale(
    stored: CitationOrInterpretation,
    parsed: float,
    factor: Decimal | None,
    *,
    squared: bool,
) -> float:
    """Multiply by the trait factor. A factor of 1 is already grams or millimetres."""
    if factor is None or factor == 1:
        return parsed
    return scale_measure(stored_text(stored), factor, squared=squared)


def _central(value: Interpretation | None) -> CentralKind | None:
    if value is None:
        return None
    return match_central(value.value)


def _dispersion_kind(value: Interpretation | None) -> DispersionKind | None:
    if value is None:
        return None
    return match_dispersion_kind(value.value)


def _parse_dispersion(
    value: CitationOrInterpretation | None, warnings: list[Notice]
) -> float | None:
    if value is None:
        return 0.0
    parsed = parse_decimal(stored_text(value))
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
    parsed = parse_signed_decimal(stored_text(value))
    if parsed is None:
        warnings.append(NotNumeric(field=field))
        return None
    return parsed
