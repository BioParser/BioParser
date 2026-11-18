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
    occurs once inside the sentence on a token boundary, and ``text`` is exactly
    that slice. The trait is an interpretation whose passage is the sentence.
    Another extractor may find ranges another way. The artifact does not record
    how.

    A measurement whose fields cannot all be located is dropped. The artifact
    requires a range on every citation.
    """
    located = (_observation(item, parser_artifact) for item in extraction.observations)
    return [observation for observation in located if observation is not None]


def _clean_block_id(block_id: str) -> str:
    cleaned = block_id.strip()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        return cleaned[1:-1].strip()
    return cleaned


def _observation(
    observation: ModelObservation, parser_artifact: ParserArtifact
) -> Observation | None:
    block_id = _clean_block_id(observation.evidence_block_id)
    block_text = _block_text(parser_artifact, block_id)
    quotation = observation.quotation
    passage = _quotation(block_text, block_id, quotation)
    if passage is None or block_text is None:
        return None
    quote_range = passage.evidence[0]
    name = _cited(block_text, block_id, quote_range.start, quote_range.end, observation.taxon)
    value = _cited(block_text, block_id, quote_range.start, quote_range.end, observation.value)
    unit = _cited(block_text, block_id, quote_range.start, quote_range.end, observation.unit)
    if name is None or value is None or unit is None:
        return None
    trait = (
        Interpretation(value=observation.trait_code, passage=passage)
        if observation.trait_code
        else None
    )
    return Observation(
        trait=trait,
        verbatimScientificName=name,
        verbatimTraitValue=value,
        verbatimTraitUnit=unit,
    )


def _is_boundary_left(block_text: str, start: int) -> bool:
    """Token boundary to the left: not letters, digits, or '.' connecting tokens."""
    if start == 0:
        return True
    c = block_text[start - 1]
    return not (c.isalnum() or (c == "." and start >= 2 and block_text[start - 2].isalnum()))


def _is_boundary_right(block_text: str, end: int) -> bool:
    """Token boundary to the right: not letters, digits, or '.' connecting tokens."""
    if end == len(block_text):
        return True
    c = block_text[end]
    return not (
        c.isalnum() or (c == "." and end + 1 < len(block_text) and block_text[end + 1].isalnum())
    )


def _cited(
    block_text: str,
    block_id: str,
    quote_start: int,
    quote_end: int,
    needle: str,
) -> Citation | None:
    span = _unique_token_span(block_text, quote_start, quote_end, needle)
    if span is None:
        return None
    start, end = span
    return Citation(text=needle, evidence=[TextRange(block_id=block_id, start=start, end=end)])


def _quotation(block_text: str | None, block_id: str, quotation: str) -> Citation | None:
    if block_text is None:
        return None
    span = _unique_span(block_text, quotation, 0)
    if span is None:
        return None
    start, end = span
    return Citation(text=quotation, evidence=[TextRange(block_id=block_id, start=start, end=end)])


def _unique_token_span(
    block_text: str, quote_start: int, quote_end: int, needle: str
) -> tuple[int, int] | None:
    if needle == "":
        return None
    haystack = block_text[quote_start:quote_end]
    found: int | None = None
    search_pos = 0
    while True:
        index = haystack.find(needle, search_pos)
        if index < 0:
            break
        abs_start = quote_start + index
        abs_end = abs_start + len(needle)

        left_ok = _is_boundary_left(block_text, abs_start)
        right_ok = _is_boundary_right(block_text, abs_end)

        if left_ok and right_ok:
            if found is not None:
                return None  # Ambiguous: occurs more than once on a token boundary
            found = abs_start
        search_pos = index + 1

    if found is None:
        return None
    return found, found + len(needle)


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


def _block_text(parser_artifact: ParserArtifact, block_id: str) -> str | None:
    for page in parser_artifact.pages:
        for block in page.blocks:
            if block.block_id == block_id and isinstance(block.content, TextContent):
                return block.content.text
    return None
