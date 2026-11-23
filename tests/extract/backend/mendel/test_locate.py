from bioparser.extract.backend.mendel.locate import locate_in_block, locate_in_page
from bioparser.extract.backend.mendel.schema import ModelObservation
from bioparser.extract.citations import reject_mismatched_ranges
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

SENTENCE = "Mus musculus adults averaged a body mass of 12.5 kg."


def _paper(text: str) -> ParserArtifact:
    return ParserArtifact(
        schema_version=PARSER_ARTIFACT_SCHEMA_VERSION,
        checksum="a" * 64,
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


def _measurement(**changes: str) -> ModelObservation:
    fields = {
        "verbatim_scientific_name": "Mus musculus",
        "trait_name": "body mass",
        "value": "12.5",
        "unit": "kg",
    }
    fields.update(changes)
    return ModelObservation.model_validate(fields)


def test_a_unique_string_is_the_printed_slice() -> None:
    [located] = locate_in_block([_measurement()], block_id="b0", block_text=SENTENCE)

    assert located.verbatimTraitValue is not None
    assert located.verbatimTraitValue.text == "12.5"
    assert SENTENCE[located.verbatimTraitValue.evidence[0].start :].startswith("12.5")
    reject_mismatched_ranges([located], _paper(SENTENCE))


def test_a_measurement_may_omit_the_printed_name() -> None:
    [located] = locate_in_block(
        [_measurement(verbatim_scientific_name="")],
        block_id="b0",
        block_text=SENTENCE,
    )

    assert located.verbatimScientificName is None
    assert located.verbatimTraitName.text == "body mass"


def test_a_name_that_is_not_in_the_text_drops_the_row() -> None:
    assert (
        locate_in_page([_measurement(verbatim_scientific_name="Sorex")], [("b0", SENTENCE)]) == []
    )


def test_a_repeated_unit_without_a_choice_is_left_empty() -> None:
    text = "The mass was 12.5 kg and the length was 9 kg."
    [located] = locate_in_block(
        [_measurement(verbatim_scientific_name="", trait_name="mass")],
        block_id="b0",
        block_text=text,
    )

    assert located.verbatimTraitValue is not None
    assert located.verbatimTraitValue.text == "12.5"
    assert located.verbatimTraitUnit is None
