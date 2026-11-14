import pytest
from pydantic import ValidationError

from bioparser.extract.backend.default.schema import Extraction, ModelObservation
from bioparser.extract.models import (
    Citation,
    Correction,
    ExtractionArtifact,
    Interpretation,
    Observation,
    TextRange,
)


def _trait(value: str = "body_mass") -> Interpretation:
    return Interpretation(value=value, passage=_cite("weighed 12.5 g"))


def _cite(text: str, start: int | None = None, end: int | None = None) -> Citation:
    if start is None or end is None:
        start, end = 0, len(text)
    return Citation(text=text, evidence=[TextRange(block_id="b0", start=start, end=end)])


def _observation(**changes: object) -> Observation:
    observation = Observation(
        traitCode=_trait(),
        verbatimTraitName=_cite("body mass"),
        verbatimScientificName=_cite("Mus musculus"),
        verbatimTraitValue=_cite("12.5", 0, 4),
        verbatimTraitUnit=_cite("g", 5, 6),
    )
    return observation.model_copy(update=changes)


def test_a_reversed_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TextRange(block_id="b0", start=4, end=2)


def test_an_empty_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TextRange(block_id="b0", start=4, end=4)


def test_empty_text_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Citation(text="", evidence=[TextRange(block_id="b0", start=0, end=1)])


def test_a_mismatched_unit_is_stored_as_printed() -> None:
    observation = Observation(
        traitCode=_trait("body_length"),
        verbatimTraitName=_cite("body length"),
        verbatimScientificName=_cite("Mus musculus"),
        verbatimTraitValue=_cite("12.5"),
        verbatimTraitUnit=_cite("g"),
    )

    stored = observation.model_dump()
    assert stored["verbatimTraitUnit"]["text"] == "g"
    assert "unit_ok" not in stored


def test_the_trait_is_any_non_empty_string() -> None:
    observation = _observation(traitCode=_trait("wing_length"))

    assert observation.traitCode is not None
    assert observation.traitCode.value == "wing_length"


def test_a_citation_without_a_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Citation.model_validate({"text": "12.5"})

    with pytest.raises(ValidationError):
        Citation.model_validate({"text": "12.5", "evidence": []})


def test_the_stored_artifact_has_no_observation_cap() -> None:
    observation = Observation(
        traitCode=_trait(),
        verbatimTraitName=_cite("body mass"),
        verbatimScientificName=_cite("Mus musculus"),
        verbatimTraitValue=_cite("12.5"),
        verbatimTraitUnit=_cite("g"),
    )
    artifact = ExtractionArtifact(
        extractor="default",
        extractor_version="1",
        observations=[observation for _ in range(9)],
    )

    assert len(artifact.observations) == 9


def test_the_model_payload_keeps_its_observation_cap() -> None:
    observation = ModelObservation(
        taxon="Mus musculus",
        trait_code="body_mass",
        value=12.5,
        unit="g",
        evidence_block_id="b0",
        quotation="12.5 g",
    )

    with pytest.raises(ValidationError):
        Extraction(observations=[observation for _ in range(9)])


def test_a_choice_value_need_not_occur_in_its_passage() -> None:
    observation = _observation(
        individualCount=Interpretation(value="12", passage=_cite("8 males and 4 females", 0, 22))
    )

    count = observation.individualCount
    assert isinstance(count, Interpretation)
    assert count.value == "12"


def test_a_copied_field_keeps_the_narrow_citation() -> None:
    observation = _observation(
        lifeStage=Correction(
            citation=_cite("adult"),
            interpretation=Interpretation(value="adult", passage=_cite("adult males")),
        )
    )

    stage = observation.lifeStage
    assert isinstance(stage, Correction)
    assert stage.citation.text == "adult"
    assert stage.interpretation.value == "adult"
    assert stage.interpretation.passage.text == "adult males"


def test_a_missing_trait_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Observation.model_validate(
            {
                "traitCode": {
                    "kind": "interpretation",
                    "value": "body_mass",
                    "passage": {"text": "weighed"},
                },
                "verbatimScientificName": {"kind": "citation", "text": "Mus musculus"},
                "verbatimTraitValue": {"kind": "citation", "text": "12.5"},
            }
        )


def test_a_paired_value_may_be_omitted() -> None:
    observation = _observation()

    assert observation.scientificName is None
    assert observation.traitValue is None
    assert observation.traitUnit is None


def test_a_scientific_name_can_expand_the_printed_citation() -> None:
    observation = _observation(
        verbatimScientificName=_cite("M. musculus"),
        scientificName=Interpretation(value="Mus musculus", passage=_cite("M. musculus")),
    )

    assert isinstance(observation.scientificName, Interpretation)
    assert observation.scientificName.value == "Mus musculus"
    assert observation.verbatimScientificName.text == "M. musculus"


def test_a_paired_value_requires_its_verbatim_citation() -> None:
    with pytest.raises(ValidationError):
        Observation.model_validate(
            {
                "traitCode": {
                    "kind": "interpretation",
                    "value": "body_mass",
                    "passage": {"text": "weighed"},
                },
                "verbatimTraitName": {"kind": "citation", "text": "body mass"},
                "verbatimScientificName": {"kind": "citation", "text": "Mus musculus"},
                "traitValue": {
                    "kind": "interpretation",
                    "value": "12.5",
                    "passage": {"text": "12.5 g"},
                },
                "verbatimTraitUnit": {"kind": "citation", "text": "g"},
            }
        )
