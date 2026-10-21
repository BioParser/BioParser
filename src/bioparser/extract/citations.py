"""Check that a stored range is the slice it names.

The schema cannot do this. It does not open the parser artifact.
"""

from __future__ import annotations

from bioparser.extract.errors import ExtractionFailedError
from bioparser.extract.models import Cited, Observation
from bioparser.parser.models import ParserArtifact, TextContent


def reject_mismatched_ranges(
    observations: list[Observation], parser_artifact: ParserArtifact
) -> None:
    """Reject a claimed range that is not the slice it names.

    A field with no range is the extractor's choice and is left unchanged.
    """
    blocks = _text_by_block_id(parser_artifact)
    for observation in observations:
        for name in type(observation).model_fields:
            value = getattr(observation, name)
            if not isinstance(value, Cited) or value.evidence is None:
                continue
            evidence = value.evidence
            text = blocks.get(evidence.block_id)
            if text is None or text[evidence.start : evidence.end] != value.text:
                raise ExtractionFailedError("cited text does not match its range")


def _text_by_block_id(parser_artifact: ParserArtifact) -> dict[str, str]:
    found: dict[str, str] = {}
    for page in parser_artifact.pages:
        for block in page.blocks:
            if block.block_id in found or not isinstance(block.content, TextContent):
                continue
            found[block.block_id] = block.content.text
    return found
