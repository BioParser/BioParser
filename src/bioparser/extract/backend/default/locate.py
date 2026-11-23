"""Turn this backend's model output into stored observations."""

from __future__ import annotations

from bioparser.extract.backend.default.schema import Extraction, ModelObservation
from bioparser.extract.models import Citation, Observation, TextRange
from bioparser.parser.models import ParserArtifact, TextContent


def cited_observations(
    extraction: Extraction, parser_artifact: ParserArtifact
) -> list[Observation]:
    """The quotation only locates each verbatim field.

    The model does not give character offsets. It names one block and quotes a
    sentence from it. The sentence is a locator and is not stored. A field gets
    a range when the sentence occurs once in the block and the field string
    occurs once inside the sentence on a token boundary, and ``text`` is exactly
    that slice. Another extractor may find ranges another way. The artifact does
    not record how.

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
    trait_name = _cited(
        block_text, block_id, quote_range.start, quote_range.end, observation.trait_name
    )
    name = _cited(block_text, block_id, quote_range.start, quote_range.end, observation.taxon)
    value = _cited(block_text, block_id, quote_range.start, quote_range.end, observation.value)
    if observation.unit is not None:
        unit = _cited(block_text, block_id, quote_range.start, quote_range.end, observation.unit)
        if unit is None:
            return None
    else:
        unit = None
    if trait_name is None or name is None or value is None:
        return None
    return Observation(
        verbatimTraitName=trait_name,
        verbatimScientificName=name,
        verbatimTraitValue=value,
        verbatimTraitUnit=unit,
    )


def _same_token_chars(a: str, b: str) -> bool:
    """True when adjacent characters belong to the same token (digit/letter switch is a boundary)."""
    return (a.isalpha() and b.isalpha()) or (a.isdigit() and b.isdigit())


def _is_boundary_left(block_text: str, start: int) -> bool:
    if start == 0:
        return True
    left = block_text[start - 1]
    first = block_text[start]
    if _same_token_chars(left, first):
        return False
    if left in (".", ",") and start >= 2:
        prev = block_text[start - 2]
        if prev.isdigit() and first.isdigit():
            return False
        if left == "." and prev.isalpha() and first.isalpha():
            return False
    if first in (".", ",") and start + 1 < len(block_text):
        nxt = block_text[start + 1]
        if left.isdigit() and nxt.isdigit():
            return False
        if first == "." and left.isalpha() and nxt.isalpha():
            return False
    return True


def _is_boundary_right(block_text: str, end: int) -> bool:
    if end == len(block_text):
        return True
    last = block_text[end - 1]
    right = block_text[end]
    if _same_token_chars(last, right):
        return False
    if right in (".", ",") and end + 1 < len(block_text):
        nxt = block_text[end + 1]
        if last.isdigit() and nxt.isdigit():
            return False
        if right == "." and last.isalpha() and nxt.isalpha():
            return False
    if last in (".", ",") and end >= 2:
        prev = block_text[end - 2]
        if prev.isdigit() and right.isdigit():
            return False
        if last == "." and prev.isalpha() and right.isalpha():
            return False
    return True


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

        if _is_boundary_left(block_text, abs_start) and _is_boundary_right(block_text, abs_end):
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
