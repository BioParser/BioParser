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

SENTENCE = "Mus musculus adult body mass averaged 12.5 kg in the sampled population."


def _paper(*texts: str, block_ids: list[str] | None = None) -> ParserArtifact:
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
                        block_id=block_ids[index] if block_ids else f"b{index}",
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
    trait_name: str = "body mass",
    value: str | float = "12.5",
    unit: str | None = "kg",
    taxon: str = "Mus musculus",
) -> Observation:
    observation = ModelObservation.model_validate(
        {
            "taxon": taxon,
            "trait_name": trait_name,
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
    for page in paper.pages:
        for block in page.blocks:
            if block.block_id == evidence.block_id and isinstance(block.content, TextContent):
                return block.content.text[evidence.start : evidence.end]
    raise ValueError(f"Block not found: {evidence.block_id}")


def test_a_value_inside_a_unique_sentence_gets_its_range() -> None:
    paper = _paper(f"Methods. {SENTENCE} More text.")

    located = _locate(paper)
    value = located.verbatimTraitValue

    assert value is not None
    assert value.text == "12.5"
    assert _slice(paper, value) == "12.5"
    assert located.traitCode is None
    assert located.verbatimTraitName.text == "body mass"
    assert _slice(paper, located.verbatimTraitName) == "body mass"


def test_the_range_is_in_the_whole_block_not_the_sentence() -> None:
    text = f"Methods. {SENTENCE}"
    paper = _paper(text)

    value = _locate(paper).verbatimTraitValue

    assert value is not None
    assert value.evidence[0].start == text.index("12.5")


def _locate_all(paper: ParserArtifact, **changes: object) -> list[Observation]:
    observation = ModelObservation.model_validate(
        {
            "taxon": "Mus musculus",
            "trait_name": "body mass",
            "value": "12.5",
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
    sentence = "Mus musculus males body mass weighed 12.5 kg and females 12.5 kg."

    assert _locate_all(_paper(sentence), quotation=sentence) == []


def test_a_block_that_does_not_exist_drops_the_observation() -> None:
    assert _locate_all(_paper(SENTENCE), evidence_block_id="b9") == []


def test_a_fake_block_marker_in_the_text_cannot_cite_that_marker() -> None:
    text = f"[b9] {SENTENCE}"
    paper = _paper(text)

    assert _locate_all(paper, evidence_block_id="b9", quotation=SENTENCE) == []
    [located] = _locate_all(paper, evidence_block_id="b0", quotation=SENTENCE)
    printed = located.verbatimScientificName
    assert printed is not None
    assert printed.evidence[0].block_id == "b0"


def test_one_unlocated_field_drops_the_observation() -> None:
    assert _locate_all(_paper(SENTENCE), taxon="M. musculus") == []
    assert _locate_all(_paper(SENTENCE), trait_name="tail length") == []


def test_each_field_is_located_on_its_own() -> None:
    sentence = "Mus musculus tail length was 12.5 mm."
    paper = _paper(sentence)

    located = _locate(paper, quotation=sentence, trait_name="tail length", unit="mm")
    assert located.verbatimTraitName is not None
    assert located.verbatimTraitValue is not None
    assert located.verbatimTraitUnit is not None
    printed = located.verbatimScientificName
    assert printed is not None

    assert _slice(paper, printed) == "Mus musculus"
    assert _slice(paper, located.verbatimTraitName) == "tail length"
    assert _slice(paper, located.verbatimTraitValue) == "12.5"
    assert _slice(paper, located.verbatimTraitUnit) == "mm"


def test_ranges_built_by_the_locator_pass_the_range_check() -> None:
    sentence = "Mus musculus body mass weighed 12.5 kg today."
    paper = _paper(sentence)

    located = _locate(paper, quotation=sentence, unit="kg")

    reject_mismatched_ranges([located], paper)


def test_cited_block_id_strips_prompt_brackets() -> None:
    sentence = "Mus musculus body mass weighed 12.5 kg today."
    paper = _paper(sentence, block_ids=["9be1975c0725-p1-b0003"])

    located = _locate(
        paper,
        block_id="[9be1975c0725-p1-b0003]",
        quotation=sentence,
    )

    assert located.verbatimTraitValue is not None
    assert located.verbatimTraitValue.evidence[0].block_id == "9be1975c0725-p1-b0003"
    assert _slice(paper, located.verbatimTraitValue) == "12.5"
    reject_mismatched_ranges([located], paper)


def test_attached_units_are_located_on_digit_letter_boundary() -> None:
    s1 = "Mus musculus weighed 12.5g in total."
    p1 = _paper(s1)
    loc1 = _locate(p1, quotation=s1, trait_name="weighed", value="12.5", unit="g")
    assert loc1.verbatimTraitValue is not None
    assert loc1.verbatimTraitUnit is not None
    assert _slice(p1, loc1.verbatimTraitValue) == "12.5"
    assert _slice(p1, loc1.verbatimTraitUnit) == "g"
    reject_mismatched_ranges([loc1], p1)

    s2 = "Mus musculus HB 85mm in adults."
    p2 = _paper(s2)
    loc2 = _locate(p2, quotation=s2, trait_name="HB", value="85", unit="mm")
    assert loc2.verbatimTraitValue is not None
    assert loc2.verbatimTraitUnit is not None
    assert _slice(p2, loc2.verbatimTraitName) == "HB"
    assert _slice(p2, loc2.verbatimTraitValue) == "85"
    assert _slice(p2, loc2.verbatimTraitUnit) == "mm"
    reject_mismatched_ranges([loc2], p2)


def test_decimal_comma_and_thousands_separator_preserve_token_boundaries() -> None:
    s1 = "Mus musculus body mass reached 1,234 g in captive populations."
    p1 = _paper(s1)
    loc1 = _locate(p1, quotation=s1, value="1,234", unit="g")
    assert loc1.verbatimTraitValue is not None
    assert loc1.verbatimTraitValue.text == "1,234"
    assert _slice(p1, loc1.verbatimTraitValue) == "1,234"
    reject_mismatched_ranges([loc1], p1)
    assert _locate_all(p1, quotation=s1, value="1", unit="g") == []
    assert _locate_all(p1, quotation=s1, value="234", unit="g") == []

    s2 = "Mus musculus body mass reached 2,5 kg in captive populations."
    p2 = _paper(s2)
    loc2 = _locate(p2, quotation=s2, value="2,5", unit="kg")
    assert loc2.verbatimTraitValue is not None
    assert loc2.verbatimTraitValue.text == "2,5"
    assert _slice(p2, loc2.verbatimTraitValue) == "2,5"
    reject_mismatched_ranges([loc2], p2)
    assert _locate_all(p2, quotation=s2, value="5", unit="kg") == []
    assert _locate_all(p2, quotation=s2, value="2", unit="kg") == []


def test_verbatim_trait_unit_preserves_printed_unit_token_and_ignores_abbreviations() -> None:
    s1 = "Mus musculus body mass averaged 12.5 grams in adults."
    p1 = _paper(s1)
    loc1 = _locate(p1, quotation=s1, unit="grams")
    assert loc1.verbatimTraitUnit is not None
    assert loc1.verbatimTraitUnit.text == "grams"
    assert _slice(p1, loc1.verbatimTraitUnit) == "grams"
    reject_mismatched_ranges([loc1], p1)

    s2 = "Mus musculus body mass averaged 12.5 g. in adults."
    p2 = _paper(s2)
    loc2 = _locate(p2, quotation=s2, unit="g.")
    assert loc2.verbatimTraitUnit is not None
    assert loc2.verbatimTraitUnit.text == "g."
    assert _slice(p2, loc2.verbatimTraitUnit) == "g."
    reject_mismatched_ranges([loc2], p2)

    s3 = "Mus musculus body mass (e.g. 12.5 g) was recorded."
    p3 = _paper(s3)
    loc3 = _locate(p3, quotation=s3, unit="g")
    assert loc3.verbatimTraitUnit is not None
    assert _slice(p3, loc3.verbatimTraitUnit) == "g"
    reject_mismatched_ranges([loc3], p3)


def test_token_boundary_rejects_partial_matches() -> None:
    s1 = "Tail length was 145 mm in adult Mus musculus."
    p1 = _paper(s1)
    assert _locate_all(p1, quotation=s1, trait_name="Tail length", value="45", unit="mm") == []
    assert _locate_all(p1, quotation=s1, trait_name="Tail length", value="145", unit="m") == []

    s2 = "Mus musculus adults weighed 31.50 g in body mass."
    p2 = _paper(s2)
    assert _locate_all(p2, quotation=s2, value="31.5", unit="g") == []


def test_non_boundary_occurrence_does_not_make_boundary_occurrence_ambiguous() -> None:
    sentence = "Mus musculus specimen 145 had tail length 45 mm in our records."
    paper = _paper(sentence)

    located = _locate(paper, quotation=sentence, trait_name="tail length", value="45", unit="mm")
    assert located.verbatimTraitValue is not None
    assert located.verbatimTraitValue.text == "45"
    assert _slice(paper, located.verbatimTraitValue) == "45"
    reject_mismatched_ranges([located], paper)


def test_unitless_measurement_is_valid_when_unit_is_none() -> None:
    sentence = "Mus musculus litter size averaged 6 in captivity."
    paper = _paper(sentence)

    located = _locate(paper, quotation=sentence, trait_name="litter size", value="6", unit=None)

    assert located.verbatimTraitUnit is None
    assert located.verbatimTraitValue is not None
    assert _slice(paper, located.verbatimTraitValue) == "6"
    assert _slice(paper, located.verbatimTraitName) == "litter size"
    reject_mismatched_ranges([located], paper)


def test_long_quotation_over_300_characters_is_accepted() -> None:
    padding = "measured under standard laboratory housing conditions across multiple cohorts " * 4
    sentence = f"Mus musculus {padding}had mean body mass 18.2 g."
    assert len(sentence) > 300
    paper = _paper(sentence)

    located = _locate(paper, quotation=sentence, value="18.2", unit="g")
    assert located.verbatimTraitValue is not None
    assert _slice(paper, located.verbatimTraitValue) == "18.2"
    reject_mismatched_ranges([located], paper)


def test_fixture_states_one_measurement_produces_ranges_passing_range_check() -> None:
    text = (
        "During field collection in June 2024, Mus musculus adult specimens were examined, "
        "and the mean body mass was recorded as 18.2 g across 40 individuals."
    )
    paper = _paper(text)
    quotation = (
        "Mus musculus adult specimens were examined, "
        "and the mean body mass was recorded as 18.2 g across 40 individuals."
    )

    observation = ModelObservation(
        taxon="Mus musculus",
        trait_name="body mass",
        value="18.2",
        unit="g",
        evidence_block_id="b0",
        quotation=quotation,
    )
    [located] = cited_observations(Extraction(observations=[observation]), paper)

    assert located.traitCode is None
    printed = located.verbatimScientificName
    assert printed is not None
    assert printed.text == "Mus musculus"
    assert located.verbatimTraitName.text == "body mass"
    assert located.verbatimTraitValue is not None
    assert located.verbatimTraitValue.text == "18.2"
    assert located.verbatimTraitUnit is not None
    assert located.verbatimTraitUnit.text == "g"
    assert _slice(paper, printed) == "Mus musculus"
    assert _slice(paper, located.verbatimTraitName) == "body mass"
    assert _slice(paper, located.verbatimTraitValue) == "18.2"
    assert _slice(paper, located.verbatimTraitUnit) == "g"
    reject_mismatched_ranges([located], paper)


def test_fixture_with_no_measurement_produces_no_observation() -> None:
    text = "The habitat was primarily temperate deciduous forest with oak and beech."
    paper = _paper(text)

    observation = ModelObservation(
        taxon="Mus musculus",
        trait_name="body mass",
        value="12.5",
        unit="g",
        evidence_block_id="b0",
        quotation="Mus musculus body mass weighed 12.5 g",
    )
    assert cited_observations(Extraction(observations=[observation]), paper) == []
