import pytest
from pydantic import ValidationError

from bioparser.extract.models import Cited, ExtractionArtifact, Observation, TextRange


def _observation(**changes: object) -> Observation:
    observation = Observation(
        trait="body_mass",
        verbatimScientificName=Cited(text="Mus musculus"),
        verbatimTraitValue=Cited(
            text="12.5",
            evidence=TextRange(block_id="b0", start=0, end=4),
        ),
        verbatimTraitUnit=Cited(text="g", evidence=TextRange(block_id="b0", start=5, end=6)),
    )
    return observation.model_copy(update=changes)


def test_a_reversed_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TextRange(block_id="b0", start=4, end=2)


def test_empty_text_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Cited(text="")


def test_a_mismatched_unit_is_stored_as_printed() -> None:
    observation = Observation(
        trait="body_length",
        verbatimScientificName=Cited(text="Mus musculus"),
        verbatimTraitValue=Cited(text="12.5"),
        verbatimTraitUnit=Cited(text="g"),
    )

    stored = observation.model_dump()
    assert stored["verbatimTraitUnit"]["text"] == "g"
    assert "unit_ok" not in stored


def test_an_unknown_trait_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Observation.model_validate(
            {
                "trait": "wing_length",
                "verbatimScientificName": {"text": "Mus musculus"},
                "verbatimTraitValue": {"text": "12.5"},
                "verbatimTraitUnit": {"text": "g"},
            }
        )


def test_a_field_without_a_range_is_stored() -> None:
    artifact = ExtractionArtifact(
        extractor="default",
        extractor_version="1",
        observations=[_observation(verbatimScientificName=Cited(text="Mus musculus"))],
    )

    stored = artifact.model_dump()
    assert stored["observations"][0]["verbatimScientificName"]["evidence"] is None
    assert "canonical_value" not in stored["observations"][0]
