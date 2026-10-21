import pytest

from bioparser.extract.citations import reject_mismatched_ranges
from bioparser.extract.errors import ExtractionFailedError
from bioparser.extract.models import Cited, Observation, TextRange
from bioparser.parser.models import (
    PARSER_ARTIFACT_SCHEMA_VERSION,
    BlockKind,
    BlockRole,
    ContentBlock,
    Page,
    ParserArtifact,
    ParserInfo,
    TextContent,
)


def _paper(text: str = "12.5 g") -> ParserArtifact:
    return ParserArtifact(
        schema_version=PARSER_ARTIFACT_SCHEMA_VERSION,
        checksum="abc",
        parser=ParserInfo(name="default", version="1", configuration={}),
        pages=[
            Page(
                page_number=1,
                width=1.0,
                height=1.0,
                blocks=[
                    ContentBlock(
                        block_id="b0",
                        order=0,
                        kind=BlockKind.TEXT,
                        role=BlockRole.BODY,
                        content=TextContent(text=text),
                    )
                ],
            )
        ],
    )


def _observation(**changes: object) -> Observation:
    observation = Observation(
        trait="body_mass",
        verbatimScientificName=Cited(text="Mus musculus"),
        verbatimTraitValue=Cited(text="12.5", evidence=TextRange(block_id="b0", start=0, end=4)),
        verbatimTraitUnit=Cited(text="g", evidence=TextRange(block_id="b0", start=5, end=6)),
    )
    return observation.model_copy(update=changes)


def test_a_range_that_matches_the_block_is_stored() -> None:
    reject_mismatched_ranges([_observation()], _paper())


def test_a_range_into_a_missing_block_is_rejected() -> None:
    observation = _observation(
        verbatimTraitValue=Cited(
            text="12.5", evidence=TextRange(block_id="missing", start=0, end=4)
        )
    )

    with pytest.raises(ExtractionFailedError):
        reject_mismatched_ranges([observation], _paper())


def test_a_range_whose_text_differs_from_the_slice_is_rejected() -> None:
    observation = _observation(
        verbatimTraitValue=Cited(text="99", evidence=TextRange(block_id="b0", start=0, end=2))
    )

    with pytest.raises(ExtractionFailedError):
        reject_mismatched_ranges([observation], _paper())
