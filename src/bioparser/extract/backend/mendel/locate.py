"""Turn this backend's model output into stored observations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from bioparser.extract.backend.mendel.disambiguate import QuoteOption, Source, find_occurrences
from bioparser.extract.backend.mendel.schema import ModelObservation
from bioparser.extract.models import Citation, Observation, TextRange


def locate_in_page(
    observations: list[ModelObservation],
    blocks: Sequence[Source],
    choices: Mapping[tuple[int, str], QuoteOption] | None = None,
) -> list[Observation]:
    """Pin each reported field to the printed text it names.

    The model reports the printed words and does not give offsets. A field that
    occurs once is that occurrence. A field that occurs more than once uses the
    quote copied in the second call, and is left unresolved when that quote is
    missing. An unresolved required field drops the measurement. An unresolved
    optional field is left empty. The printed scientific name is optional. The
    artifact requires a range on every citation, and those ranges are computed here.
    """
    picked = choices or {}
    located = (
        _page_observation(index, item, blocks, picked) for index, item in enumerate(observations)
    )
    return [observation for observation in located if observation is not None]


def locate_in_block(
    observations: list[ModelObservation],
    *,
    block_id: str,
    block_text: str,
    choices: Mapping[tuple[int, str], QuoteOption] | None = None,
) -> list[Observation]:
    """Pin each reported field to a span in this block."""
    return locate_in_page(observations, [(block_id, block_text)], choices)


def _page_observation(
    index: int,
    observation: ModelObservation,
    blocks: Sequence[Source],
    choices: Mapping[tuple[int, str], QuoteOption],
) -> Observation | None:
    def pick(field: str, needle: str) -> tuple[str, tuple[int, int]] | None:
        return _resolve(blocks, needle, choices.get((index, field)))

    reported_name = observation.verbatim_scientific_name
    name = pick("verbatim_scientific_name", reported_name) if reported_name else None
    if reported_name and name is None:
        return None
    trait = pick("trait_name", observation.trait_name)
    value = pick("value", observation.value)
    minimum = pick("minimum", observation.minimum)
    maximum = pick("maximum", observation.maximum)
    if trait is None or not (value or minimum or maximum):
        return None
    return Observation(
        verbatimTraitName=_cite(blocks, trait[0], trait[1]),
        verbatimScientificName=_cite(blocks, name[0], name[1]) if name is not None else None,
        verbatimTraitValue=_cite(blocks, value[0], value[1]) if value is not None else None,
        verbatimTraitUnit=_optional(blocks, observation.unit, pick("unit", observation.unit)),
        dispersion=_optional(
            blocks, observation.dispersion, pick("dispersion", observation.dispersion)
        ),
        statisticalMethod=_optional(
            blocks,
            observation.statistical_method,
            pick("statistical_method", observation.statistical_method),
        ),
        measurementValueMin=_optional(blocks, observation.minimum, minimum),
        measurementValueMax=_optional(blocks, observation.maximum, maximum),
    )


def _optional(
    blocks: Sequence[Source],
    text: str,
    found: tuple[str, tuple[int, int]] | None,
) -> Citation | None:
    """A citation when the model reported the field and one occurrence was chosen."""
    if not text or found is None:
        return None
    block_id, span = found
    return _cite(blocks, block_id, span)


def _resolve(
    blocks: Sequence[Source],
    needle: str,
    choice: QuoteOption | None,
) -> tuple[str, tuple[int, int]] | None:
    """The single occurrence, or the occurrence whose quote was copied."""
    if not needle:
        return None
    found = find_occurrences(blocks, needle)
    if len(found) == 1:
        return found[0].block_id, (found[0].start, found[0].end)
    if choice is None:
        return None
    for item in found:
        if (
            item.block_id == choice.block_id
            and item.start == choice.start
            and item.end == choice.end
        ):
            return item.block_id, (item.start, item.end)
    return None


def _cite(blocks: Sequence[Source], block_id: str, span: tuple[int, int]) -> Citation:
    start, end = span
    for source_id, text, origin in _placed(blocks):
        if source_id == block_id and origin <= start and end <= origin + len(text):
            local = start - origin
            return Citation(
                text=text[local : local + (end - start)],
                evidence=[TextRange(block_id=block_id, start=start, end=end)],
            )
    raise ValueError("cited span is outside the shown text")


def _placed(blocks: Sequence[Source]) -> list[tuple[str, str, int]]:
    placed: list[tuple[str, str, int]] = []
    for block in blocks:
        if len(block) == 2:
            placed.append((block[0], block[1], 0))
        else:
            placed.append((block[0], block[1], block[2]))
    return placed
