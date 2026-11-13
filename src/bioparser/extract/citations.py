"""Check that a stored range is the slice it names.

The schema cannot do this. It does not open the parser artifact.
"""

from __future__ import annotations

from bioparser.extract.errors import ExtractionFailedError
from bioparser.extract.models import (
    Citation,
    Correction,
    Interpretation,
    Observation,
    TextRange,
)
from bioparser.parser.models import ParserArtifact, TextContent


def reject_mismatched_ranges(
    observations: list[Observation], parser_artifact: ParserArtifact
) -> None:
    """Reject a claimed range that is not the slice it names.

    Every citation has a range list. A range must stay inside its block.
    Several ranges must follow the parser's block order, and their slices
    must join to the citation text.
    An interpretation value is not compared with its passage.
    """
    texts, order = _blocks(parser_artifact)
    for observation in observations:
        for name in type(observation).model_fields:
            _check(getattr(observation, name), texts, order)


def _check(value: object, texts: dict[str, str], order: dict[str, int]) -> None:
    if isinstance(value, Citation):
        _check_citation(value, texts, order)
    elif isinstance(value, Interpretation):
        _check_citation(value.passage, texts, order)
    elif isinstance(value, Correction):
        _check_citation(value.citation, texts, order)
        _check_citation(value.interpretation.passage, texts, order)


def _check_citation(citation: Citation, texts: dict[str, str], order: dict[str, int]) -> None:
    parts: list[str] = []
    previous: tuple[int, int] | None = None
    for evidence in citation.evidence:
        part = _slice(evidence, texts, order)
        if previous is not None and _out_of_order(previous, evidence, order):
            raise ExtractionFailedError("cited ranges are out of reading order")
        previous = (order[evidence.block_id], evidence.end)
        parts.append(part)
    if "".join(parts) != citation.text:
        raise ExtractionFailedError("cited text does not match its range")


def _slice(evidence: TextRange, texts: dict[str, str], order: dict[str, int]) -> str:
    text = texts.get(evidence.block_id)
    # Python slicing stops at the end of the string, so a range past the block
    # would otherwise look like a shorter slice.
    if text is None or evidence.block_id not in order or evidence.end > len(text):
        raise ExtractionFailedError("cited text does not match its range")
    return text[evidence.start : evidence.end]


def _out_of_order(previous: tuple[int, int], evidence: TextRange, order: dict[str, int]) -> bool:
    previous_block, previous_end = previous
    position = order[evidence.block_id]
    return position < previous_block or (
        position == previous_block and evidence.start < previous_end
    )


def _blocks(parser_artifact: ParserArtifact) -> tuple[dict[str, str], dict[str, int]]:
    texts: dict[str, str] = {}
    order: dict[str, int] = {}
    index = 0
    for page in parser_artifact.pages:
        for block in page.blocks:
            if block.block_id in texts or not isinstance(block.content, TextContent):
                continue
            texts[block.block_id] = block.content.text
            order[block.block_id] = index
            index += 1
    return texts, order
