import pytest
from pydantic import ValidationError

from bioparser.extract.backend.default.schema import Extraction, ModelObservation
from bioparser.extract.models import (
    AggregateInterpretation,
    BasisInterpretation,
    Citation,
    Correction,
    ExtractionArtifact,
    Interpretation,
    Observation,
    TextRange,
    Trait,
    TraitInterpretation,
    TraitUnitInterpretation,
)


def _trait(value: Trait = "body_mass") -> TraitInterpretation:
    return TraitInterpretation(value=value, passage=Citation(text="weighed 12.5 g"))


def _cite(text: str, start: int | None = None, end: int | None = None) -> Citation:
    if start is None or end is None:
        return Citation(text=text)
    return Citation(text=text, evidence=[TextRange(block_id="b0", start=start, end=end)])


def _observation(**changes: object) -> Observation:
    observation = Observation(
        trait=_trait(),
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
        Citation(text="")


def test_a_mismatched_unit_is_stored_as_printed() -> None:
    observation = Observation(
        trait=_trait("body_length"),
        verbatimScientificName=_cite("Mus musculus"),
        verbatimTraitValue=_cite("12.5"),
        verbatimTraitUnit=_cite("g"),
    )

    stored = observation.model_dump()
    assert stored["verbatimTraitUnit"]["text"] == "g"
    assert "unit_ok" not in stored


def test_an_unknown_trait_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Observation.model_validate(
            {
                "trait": {
                    "kind": "interpretation",
                    "value": "wing_length",
                    "passage": {"text": "wing"},
                },
                "verbatimScientificName": {"kind": "citation", "text": "Mus musculus"},
                "verbatimTraitValue": {"kind": "citation", "text": "12.5"},
                "verbatimTraitUnit": {"kind": "citation", "text": "g"},
            }
        )


def test_a_field_without_a_range_is_stored() -> None:
    artifact = ExtractionArtifact(
        extractor="default",
        extractor_version="1",
        observations=[_observation(verbatimScientificName=_cite("Mus musculus"))],
    )

    stored = artifact.model_dump()
    assert stored["observations"][0]["verbatimScientificName"]["evidence"] is None
    assert "canonical_value" not in stored["observations"][0]


def test_the_stored_artifact_has_no_observation_cap() -> None:
    observation = Observation(
        trait=_trait(),
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
        trait="body_mass",
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


def test_basis_and_aggregate_are_interpretations_only() -> None:
    observation = _observation(
        basisOfRecord=BasisInterpretation(value="FossilSpecimen", passage=_cite("fossil")),
        aggregateMeasure=AggregateInterpretation(value="TRUE", passage=_cite("mean of 12")),
    )

    assert observation.basisOfRecord is not None
    assert observation.basisOfRecord.value == "FossilSpecimen"
    assert observation.aggregateMeasure is not None
    assert observation.aggregateMeasure.value == "TRUE"

    with pytest.raises(ValidationError):
        Observation.model_validate(
            {
                "trait": {
                    "kind": "interpretation",
                    "value": "body_mass",
                    "passage": {"text": "weighed"},
                },
                "verbatimScientificName": {"kind": "citation", "text": "Mus musculus"},
                "verbatimTraitValue": {"kind": "citation", "text": "12.5"},
                "verbatimTraitUnit": {"kind": "citation", "text": "g"},
                "basisOfRecord": {"kind": "citation", "text": "LivingSpecimen"},
            }
        )
    with pytest.raises(ValidationError):
        Observation.model_validate(
            {
                "trait": {
                    "kind": "interpretation",
                    "value": "body_mass",
                    "passage": {"text": "weighed"},
                },
                "verbatimScientificName": {"kind": "citation", "text": "Mus musculus"},
                "verbatimTraitValue": {"kind": "citation", "text": "12.5"},
                "verbatimTraitUnit": {"kind": "citation", "text": "g"},
                "aggregateMeasure": {"kind": "citation", "text": "TRUE"},
            }
        )


def test_a_verbatim_field_rejects_a_choice() -> None:
    with pytest.raises(ValidationError):
        Observation.model_validate(
            {
                "trait": {
                    "kind": "interpretation",
                    "value": "body_mass",
                    "passage": {"text": "weighed"},
                },
                "verbatimScientificName": {"kind": "citation", "text": "Mus musculus"},
                "verbatimTraitValue": {
                    "kind": "interpretation",
                    "value": "12.5",
                    "passage": {"text": "weighed 12.5 g"},
                },
                "verbatimTraitUnit": {"kind": "citation", "text": "g"},
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


def test_a_paired_value_is_a_choice() -> None:
    observation = _observation(
        traitValue=Interpretation(value="12500", passage=_cite("weighed 12.5 kg")),
        traitUnit=TraitUnitInterpretation(value="g", passage=_cite("kg")),
    )

    assert isinstance(observation.traitValue, Interpretation)
    assert isinstance(observation.traitUnit, TraitUnitInterpretation)
    assert observation.verbatimTraitValue.text == "12.5"


def test_a_paired_value_requires_its_verbatim_citation() -> None:
    with pytest.raises(ValidationError):
        Observation.model_validate(
            {
                "trait": {
                    "kind": "interpretation",
                    "value": "body_mass",
                    "passage": {"text": "weighed"},
                },
                "verbatimScientificName": {"kind": "citation", "text": "Mus musculus"},
                "traitValue": {
                    "kind": "interpretation",
                    "value": "12.5",
                    "passage": {"text": "12.5 g"},
                },
                "verbatimTraitUnit": {"kind": "citation", "text": "g"},
            }
        )


def test_a_paired_field_rejects_a_second_citation() -> None:
    with pytest.raises(ValidationError):
        Observation.model_validate(
            {
                "trait": {
                    "kind": "interpretation",
                    "value": "body_mass",
                    "passage": {"text": "weighed"},
                },
                "verbatimScientificName": {"kind": "citation", "text": "Mus musculus"},
                "verbatimTraitValue": {"kind": "citation", "text": "12.5"},
                "traitValue": {"kind": "citation", "text": "12.5"},
                "verbatimTraitUnit": {"kind": "citation", "text": "g"},
            }
        )


def test_a_paired_field_rejects_a_correction() -> None:
    with pytest.raises(ValidationError):
        Observation.model_validate(
            {
                "trait": {
                    "kind": "interpretation",
                    "value": "body_mass",
                    "passage": {"text": "weighed"},
                },
                "verbatimScientificName": {"kind": "citation", "text": "Mus musculus"},
                "verbatimTraitValue": {"kind": "citation", "text": "12.5"},
                "traitValue": {
                    "kind": "correction",
                    "citation": {"kind": "citation", "text": "12.5"},
                    "choice": {
                        "kind": "interpretation",
                        "value": "12.5",
                        "passage": {"text": "12.5 g"},
                    },
                },
                "verbatimTraitUnit": {"kind": "citation", "text": "g"},
            }
        )


def test_a_trait_unit_outside_grams_or_millimetres_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Observation(
            trait=_trait(),
            verbatimScientificName=_cite("Mus musculus"),
            verbatimTraitValue=_cite("12.5"),
            verbatimTraitUnit=_cite("kg"),
            traitUnit=Citation(text="kg"),
        )
