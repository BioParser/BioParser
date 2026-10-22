import pytest

from bioparser.extract.citations import reject_mismatched_ranges
from bioparser.extract.errors import ExtractionFailedError
from bioparser.extract.models import (
    Citation,
    Correction,
    Interpretation,
    Observation,
    TextRange,
    TraitInterpretation,
)
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


def _blocks(*texts: str) -> list[ContentBlock]:
    return [
        ContentBlock(
            block_id=f"b{index}",
            order=index,
            kind=BlockKind.TEXT,
            role=BlockRole.BODY,
            content=TextContent(text=text),
        )
        for index, text in enumerate(texts)
    ]


def _paper(*texts: str) -> ParserArtifact:
    if not texts:
        texts = ("12.5 g",)
    return ParserArtifact(
        schema_version=PARSER_ARTIFACT_SCHEMA_VERSION,
        checksum="abc",
        parser=ParserInfo(name="default", version="1", configuration={}),
        pages=[
            Page(
                page_number=1,
                width=1.0,
                height=1.0,
                blocks=_blocks(*texts),
            )
        ],
    )


def _observation(**changes: object) -> Observation:
    observation = Observation(
        trait=TraitInterpretation(value="body_mass", passage=Citation(text="12.5 g")),
        verbatimScientificName=Citation(text="Mus musculus"),
        verbatimTraitValue=Citation(
            text="12.5", evidence=[TextRange(block_id="b0", start=0, end=4)]
        ),
        verbatimTraitUnit=Citation(text="g", evidence=[TextRange(block_id="b0", start=5, end=6)]),
    )
    return observation.model_copy(update=changes)


def test_a_range_that_matches_the_block_is_stored() -> None:
    reject_mismatched_ranges([_observation()], _paper())


def test_a_range_into_a_missing_block_is_rejected() -> None:
    observation = _observation(
        verbatimTraitValue=Citation(
            text="12.5", evidence=[TextRange(block_id="missing", start=0, end=4)]
        )
    )

    with pytest.raises(ExtractionFailedError):
        reject_mismatched_ranges([observation], _paper())


def test_a_range_whose_text_differs_from_the_slice_is_rejected() -> None:
    observation = _observation(
        verbatimTraitValue=Citation(text="99", evidence=[TextRange(block_id="b0", start=0, end=2)])
    )

    with pytest.raises(ExtractionFailedError):
        reject_mismatched_ranges([observation], _paper())


def test_a_passage_split_across_a_column_joins_in_order() -> None:
    observation = _observation(
        verbatimTraitValue=Citation(text="12.5"),
        verbatimTraitUnit=Citation(text="g"),
        individualCount=Interpretation(
            value="12",
            passage=Citation(
                text="8 males4 females",
                evidence=[
                    TextRange(block_id="b0", start=0, end=7),
                    TextRange(block_id="b1", start=0, end=9),
                ],
            ),
        ),
    )

    reject_mismatched_ranges([observation], _paper("8 males", "4 females"))


def test_ranges_that_skip_backward_are_rejected() -> None:
    observation = _observation(
        verbatimTraitValue=Citation(text="12.5"),
        verbatimTraitUnit=Citation(text="g"),
        individualCount=Interpretation(
            value="12",
            passage=Citation(
                text="ignored",
                evidence=[
                    TextRange(block_id="b1", start=0, end=2),
                    TextRange(block_id="b0", start=0, end=7),
                ],
            ),
        ),
    )

    with pytest.raises(ExtractionFailedError):
        reject_mismatched_ranges([observation], _paper("8 males", "10 females"))


def test_a_correction_checks_the_narrow_citation() -> None:
    observation = _observation(
        verbatimTraitValue=Citation(text="12.5"),
        verbatimTraitUnit=Citation(text="g"),
        lifeStage=Correction(
            citation=Citation(text="adult", evidence=[TextRange(block_id="b0", start=0, end=5)]),
            interpretation=Interpretation(value="adult", passage=Citation(text="adult males")),
        ),
    )

    with pytest.raises(ExtractionFailedError):
        reject_mismatched_ranges([observation], _paper("12.5 g"))
