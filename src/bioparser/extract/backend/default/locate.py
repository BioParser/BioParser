"""Turn this backend's model output into stored observations."""

from __future__ import annotations

from bioparser.extract.backend.default.schema import Extraction, ModelObservation
from bioparser.extract.models import Citation, Interpretation, Observation, TextRange
from bioparser.parser.models import ParserArtifact, TextContent


def cited_observations(
    extraction: Extraction, parser_artifact: ParserArtifact
) -> list[Observation]:
    """The quotation only locates each verbatim field.

    The model does not give character offsets. It names one block and quotes a
    sentence from it. The sentence is a locator and is not stored. A field gets
    a range when the sentence occurs once in the block and the field string
    occurs once inside the sentence, and ``text`` is exactly that slice. The
    trait code is an interpretation whose passage is the sentence. Another extractor
    may find ranges another way. The artifact does not record how.

    A measurement whose fields cannot all be located is dropped. The artifact
    requires a range on every citation.
    """
    located = (_observation(item, parser_artifact) for item in extraction.observations)
    return [observation for observation in located if observation is not None]


def _observation(
    observation: ModelObservation, parser_artifact: ParserArtifact
) -> Observation | None:
    block_id = observation.evidence_block_id
    block_text = _block_text(parser_artifact, block_id)
    quotation = observation.quotation
    passage = _quotation(block_text, block_id, quotation)
    name = _cited(block_text, block_id, quotation, observation.taxon)
    value = _cited(block_text, block_id, quotation, _number_text(observation.value))
    unit = _cited(block_text, block_id, quotation, observation.unit)
    if passage is None or name is None or value is None or unit is None:
        return None
    return Observation(
        traitCode=Interpretation(value=observation.trait_code, passage=passage),
        verbatimScientificName=name,
        verbatimTraitValue=value,
        verbatimTraitUnit=unit,
    )


def _cited(block_text: str | None, block_id: str, quotation: str, value: str) -> Citation | None:
    span = _field_span(block_text, quotation, value)
    if span is None:
        return None
    start, end = span
    return Citation(text=value, evidence=[TextRange(block_id=block_id, start=start, end=end)])


def _quotation(block_text: str | None, block_id: str, quotation: str) -> Citation | None:
    if block_text is None:
        return None
    span = _unique_span(block_text, quotation, 0)
    if span is None:
        return None
    start, end = span
    return Citation(text=quotation, evidence=[TextRange(block_id=block_id, start=start, end=end)])


def _field_span(block_text: str | None, quotation: str, value: str) -> tuple[int, int] | None:
    if block_text is None:
        return None
    quote = _unique_span(block_text, quotation, 0)
    if quote is None:
        return None
    quote_start, quote_end = quote
    return _unique_span(block_text[quote_start:quote_end], value, quote_start)


def _unique_span(haystack: str, needle: str, origin: int) -> tuple[int, int] | None:
    if needle == "":
        return None
    found: int | None = None
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            break
        if found is not None:
            return None
        found = index
        start = index + 1
    if found is None:
        return None
    return origin + found, origin + found + len(needle)


def _number_text(value: float) -> str:
    return format(value, "g")


def _block_text(parser_artifact: ParserArtifact, block_id: str) -> str | None:
    for page in parser_artifact.pages:
        for block in page.blocks:
            if block.block_id == block_id and isinstance(block.content, TextContent):
                return block.content.text
    return None
