from bioparser.extract.backend.default.locate import cited_observations
from bioparser.extract.backend.default.schema import Extraction, ModelObservation
from bioparser.extract.citations import reject_mismatched_ranges
from bioparser.extract.models import Citation, Observation
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

SENTENCE = "Mus musculus adults averaged 12.5 kg in the sampled population."


def _paper(*texts: str) -> ParserArtifact:
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
    unit: str = "kg",
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


def _slice(paper: ParserArtifact, field: Citation) -> str:
    assert field.evidence is not None
    evidence = field.evidence[0]
    block = paper.pages[0].blocks[int(evidence.block_id[1:])]
    assert isinstance(block.content, TextContent)
    return block.content.text[evidence.start : evidence.end]


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
    assert value.evidence[0].start == text.index("12.5")


def _locate_all(paper: ParserArtifact, **changes: object) -> list[Observation]:
    observation = ModelObservation.model_validate(
        {
            "taxon": "Mus musculus",
            "trait": "body_mass",
            "value": 12.5,
            "unit": "kg",
            "evidence_block_id": "b0",
            "quotation": SENTENCE,
            **changes,
        }
    )
    return cited_observations(Extraction(observations=[observation]), paper)


def test_a_sentence_missing_from_the_block_drops_the_observation() -> None:
    assert _locate_all(_paper("Nothing about mass here.")) == []


def test_a_sentence_that_occurs_twice_drops_the_observation() -> None:
    assert _locate_all(_paper(f"{SENTENCE} {SENTENCE}")) == []


def test_a_value_that_occurs_twice_in_the_sentence_drops_the_observation() -> None:
    sentence = "Mus musculus males weighed 12.5 kg and females 12.5 kg."

    assert _locate_all(_paper(sentence), quotation=sentence) == []


def test_a_block_that_does_not_exist_drops_the_observation() -> None:
    assert _locate_all(_paper(SENTENCE), evidence_block_id="b9") == []


def test_one_unlocated_field_drops_the_observation() -> None:
    assert _locate_all(_paper(SENTENCE), taxon="M. musculus") == []


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
