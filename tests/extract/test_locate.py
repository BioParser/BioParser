from bioparser.extract.backend.default.locate import cited_observations
from bioparser.extract.backend.default.schema import Extraction, ModelObservation
from bioparser.extract.citations import reject_mismatched_ranges
from bioparser.extract.models import Cited, Observation
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

SENTENCE = "Adult body mass averaged 12.5 g in the sampled population."


def _paper(*texts: str) -> ParserArtifact:
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
                        block_id=f"b{index}",
                        order=index,
                        kind=BlockKind.TEXT,
                        role=BlockRole.BODY,
                        content=TextContent(text=text),
                    )
                    for index, text in enumerate(texts)
                ],
            )
        ],
    )


def _locate(
    paper: ParserArtifact,
    *,
    quotation: str = SENTENCE,
    block_id: str = "b0",
    value: float = 12.5,
    unit: str = "g",
    taxon: str = "Mus musculus",
) -> Observation:
    observation = ModelObservation.model_validate(
        {
            "taxon": taxon,
            "trait": "body_mass",
            "value": value,
            "unit": unit,
            "evidence_block_id": block_id,
            "quotation": quotation,
        }
    )
    [located] = cited_observations(Extraction(observations=[observation]), paper)
    return located


def _slice(paper: ParserArtifact, field: Cited) -> str:
    assert field.evidence is not None
    block = paper.pages[0].blocks[int(field.evidence.block_id[1:])]
    assert isinstance(block.content, TextContent)
    return block.content.text[field.evidence.start : field.evidence.end]


def test_a_value_inside_a_unique_sentence_gets_its_range() -> None:
    paper = _paper(f"Methods. {SENTENCE} More text.")

    value = _locate(paper).verbatimTraitValue

    assert value.text == "12.5"
    assert _slice(paper, value) == "12.5"


def test_the_range_is_in_the_whole_block_not_the_sentence() -> None:
    text = f"Methods. {SENTENCE}"
    paper = _paper(text)

    value = _locate(paper).verbatimTraitValue

    assert value.evidence is not None
    assert value.evidence.start == text.index("12.5")


def test_a_sentence_missing_from_the_block_stores_the_text_without_a_range() -> None:
    paper = _paper("Nothing about mass here.")

    located = _locate(paper)

    assert located.verbatimTraitValue.text == "12.5"
    assert located.verbatimTraitValue.evidence is None
    assert located.verbatimScientificName.evidence is None


def test_a_sentence_that_occurs_twice_is_not_located() -> None:
    paper = _paper(f"{SENTENCE} {SENTENCE}")

    assert _locate(paper).verbatimTraitValue.evidence is None


def test_a_value_that_occurs_twice_in_the_sentence_is_not_located() -> None:
    sentence = "Males weighed 12.5 g and females 12.5 g."
    paper = _paper(sentence)

    assert _locate(paper, quotation=sentence).verbatimTraitValue.evidence is None


def test_a_block_that_does_not_exist_stores_the_text_without_a_range() -> None:
    paper = _paper(SENTENCE)

    located = _locate(paper, block_id="b9")

    assert located.verbatimTraitValue.text == "12.5"
    assert located.verbatimTraitValue.evidence is None


def test_each_field_is_located_on_its_own() -> None:
    sentence = "Mus musculus weighed 12.5 mm."
    paper = _paper(sentence)

    located = _locate(paper, quotation=sentence, unit="mm")

    assert _slice(paper, located.verbatimScientificName) == "Mus musculus"
    assert _slice(paper, located.verbatimTraitValue) == "12.5"
    assert _slice(paper, located.verbatimTraitUnit) == "mm"


def test_ranges_built_by_the_locator_pass_the_range_check() -> None:
    sentence = "Mus musculus weighed 12.5 kg today."
    paper = _paper(sentence)

    located = _locate(paper, quotation=sentence, unit="kg")

    reject_mismatched_ranges([located], paper)
