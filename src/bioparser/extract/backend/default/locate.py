"""Turn this backend's model output into stored observations."""

from __future__ import annotations

from bioparser.extract.backend.default.schema import Extraction, ModelObservation
from bioparser.extract.models import Citation, Observation, TextRange, TraitInterpretation
from bioparser.parser.models import ParserArtifact, TextContent


def cited_observations(
    extraction: Extraction, parser_artifact: ParserArtifact
) -> list[Observation]:
    """The quotation only locates each verbatim field."""
    return [_observation(item, parser_artifact) for item in extraction.observations]


def _observation(observation: ModelObservation, parser_artifact: ParserArtifact) -> Observation:
    block_id = observation.evidence_block_id
    block_text = _block_text(parser_artifact, block_id)
    return Observation(
        trait=TraitInterpretation(
            value=observation.trait,
            passage=_quotation(block_text, block_id, observation.quotation),
        ),
        verbatimScientificName=_cited(
            block_text, block_id, observation.quotation, observation.taxon
        ),
        verbatimTraitValue=_cited(
            block_text, block_id, observation.quotation, _number_text(observation.value)
        ),
        verbatimTraitUnit=_cited(block_text, block_id, observation.quotation, observation.unit),
    )


def _cited(block_text: str | None, block_id: str, quotation: str, value: str) -> Citation:
    span = _field_span(block_text, quotation, value)
    if span is None:
        return Citation(text=value)
    start, end = span
    return Citation(text=value, evidence=[TextRange(block_id=block_id, start=start, end=end)])


def _quotation(block_text: str | None, block_id: str, quotation: str) -> Citation:
    if block_text is None:
        return Citation(text=quotation)
    span = _unique_span(block_text, quotation, 0)
    if span is None:
        return Citation(text=quotation)
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
