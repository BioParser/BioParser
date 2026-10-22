import pytest
from pydantic import ValidationError

from bioparser.ets.adapter import to_ets
from bioparser.ets.assess import Dropped, assess
from bioparser.ets.dates import parse_date
from bioparser.ets.models import EtsRow
from bioparser.ets.warnings import NotNumeric
from bioparser.extract.models import (
    AggregateInterpretation,
    BasisInterpretation,
    Citation,
    ExtractionArtifact,
    Interpretation,
    Observation,
    TextRange,
    TraitInterpretation,
    TraitUnitInterpretation,
)

_DOCUMENT_ID = "abc123"


def _cited(text: str, *, located: bool = True, start: int = 0) -> Citation:
    if not located:
        return Citation(text=text)
    return Citation(
        text=text, evidence=[TextRange(block_id="b0", start=start, end=start + len(text))]
    )


def _observation(**changes: object) -> Observation:
    observation = Observation(
        trait=TraitInterpretation(value="body_mass", passage=_cited("body mass", start=10)),
        verbatimScientificName=_cited("M. musculus", start=0),
        verbatimTraitValue=_cited("12.5", start=20),
        verbatimTraitUnit=_cited("kg", start=25),
        individualCount=_cited("25", start=30),
        measurementValue_min=_cited("1.5", start=40),
    )
    if not changes:
        return observation
    return observation.model_copy(update=changes)


def _artifact(*observations: Observation) -> ExtractionArtifact:
    return ExtractionArtifact(
        extractor="default",
        extractor_version="1",
        observations=list(observations),
    )


def _row(**changes: object) -> EtsRow:
    values: dict[str, object] = {
        "scientificName": "Mus musculus",
        "verbatimScientificName": "Mus musculus",
        "verbatimTraitValue": "12.5",
        "verbatimTraitUnit": "g",
        "traitValue": "12.5",
        "traitUnit": "g",
        "references": _DOCUMENT_ID,
        "measurementID": "1",
        "basisOfRecord": "literatureData",
        "warnings": "",
    }
    values.update(changes)
    return EtsRow.model_validate(values)


def test_a_located_observation_becomes_one_ets_row() -> None:
    observation = _observation()
    artifact = _artifact(observation)
    before = artifact.model_dump()

    rows = to_ets(artifact, references=_DOCUMENT_ID)

    assert artifact.model_dump() == before
    assert len(rows) == 1
    row = rows[0]
    dumped = row.model_dump()
    assert dumped["traitValue"] == "12500"
    assert dumped["traitUnit"] == "g"
    assert dumped["scientificName"] == "M. musculus"
    assert dumped["verbatimScientificName"] == "M. musculus"
    assert dumped["taxonID"] is None
    assert dumped["traitName"] is None
    assert dumped["traitID"] is None
    assert dumped["basisOfRecord"] == "literatureData"
    assert dumped["references"] == _DOCUMENT_ID
    assert dumped["measurementID"] == "1"
    assert dumped["occurrenceID"] is None
    assert dumped["individualCount"] == 25
    assert dumped["measurementValue_min"] == 1.5
    assert dumped["dispersion"] == 0
    assert dumped["warnings"] == ""
    assert "evidence" not in dumped
    assert "trait" not in dumped
    aliases = {field.alias or name for name, field in EtsRow.model_fields.items()}
    assert set(dumped) == aliases


def test_a_stored_converted_pair_is_not_converted_again() -> None:
    observation = _observation(
        traitValue=Interpretation(value="12500", passage=_cited("12.5 kg", start=20)),
        traitUnit=TraitUnitInterpretation(value="g", passage=_cited("12.5 kg", start=20)),
    )

    rows = to_ets(_artifact(observation), references=_DOCUMENT_ID)

    assert rows[0].traitValue == "12500"
    assert rows[0].traitUnit == "g"


def test_length_converts_to_millimetres() -> None:
    observation = _observation(
        trait=TraitInterpretation(value="tail_length", passage=_cited("tail", start=10)),
        verbatimTraitValue=_cited("2", start=20),
        verbatimTraitUnit=_cited("cm", start=25),
    )

    rows = to_ets(_artifact(observation), references=_DOCUMENT_ID)

    assert rows[0].traitValue == "20"
    assert rows[0].traitUnit == "mm"
    assert rows[0].traitName is None


def test_basis_and_aggregate_are_read_from_a_passage() -> None:
    observation = _observation(
        basisOfRecord=BasisInterpretation(
            value="FossilSpecimen", passage=_cited("fossil", start=50)
        ),
        aggregateMeasure=AggregateInterpretation(
            value="TRUE", passage=_cited("mean of 12", start=60)
        ),
    )

    rows = to_ets(_artifact(observation), references=_DOCUMENT_ID)

    assert rows[0].basisOfRecord == "FossilSpecimen"
    assert rows[0].aggregateMeasure is True


def test_a_paper_can_state_occurrence_place_and_time() -> None:
    observation = _observation(
        occurrenceID=_cited("FMNH 145732", start=70),
        year=Interpretation(value="1963", passage=_cited("collected in 1963", start=80)),
        countryCode=Interpretation(value="DE", passage=_cited("Germany", start=100)),
        eventDate=_cited("8 Mar 1963", start=110),
        measurementDeterminedDate=_cited("March 8, 1963", start=140),
        month=_cited("13", start=130),
    )

    rows = to_ets(_artifact(observation), references=_DOCUMENT_ID)

    assert rows[0].occurrenceID == "FMNH 145732"
    assert rows[0].year == 1963
    assert rows[0].countryCode == "DE"
    assert rows[0].eventDate == "1963-03-08"
    assert rows[0].measurementDeterminedDate == "1963-03-08"
    assert rows[0].month is None
    assert "not_numeric:month" in rows[0].warnings


def test_a_numeric_date_is_kept_when_one_number_cannot_be_a_month() -> None:
    assert parse_date("13/03/1963") == "1963-03-13"
    assert parse_date("03/13/1963") == "1963-03-13"
    assert parse_date("08/03/1963") is None


def test_an_empty_observation_list_returns_no_rows() -> None:
    assert to_ets(_artifact(), references=_DOCUMENT_ID) == []


def test_a_field_with_no_range_stays_on_the_row() -> None:
    observation = _observation(verbatimTraitValue=_cited("12.5", located=False))

    rows = to_ets(_artifact(observation), references=_DOCUMENT_ID)

    assert len(rows) == 1
    assert rows[0].verbatimTraitValue == "12.5"
    assert "unlocated:verbatimTraitValue" in rows[0].warnings
    assert "evidence" not in rows[0].model_dump()


def test_a_unit_that_does_not_belong_to_the_trait_drops_that_row() -> None:
    bad = _observation(verbatimTraitUnit=_cited("mm", start=25))
    good = _observation()
    before = bad.model_dump()

    verdict = assess(bad)
    rows = to_ets(_artifact(bad, good), references=_DOCUMENT_ID)

    assert bad.model_dump() == before
    assert isinstance(verdict, Dropped)
    assert verdict.drop == "unit_not_for_trait"
    assert len(rows) == 1
    assert rows[0].verbatimTraitUnit == "kg"
    assert rows[0].traitValue == "12500"
    assert rows[0].measurementID == "1"


def test_a_rejected_artifact_never_reaches_the_adapter() -> None:
    with pytest.raises(ValidationError):
        ExtractionArtifact.model_validate(
            {
                "schema_version": "1",
                "extractor": "default",
                "extractor_version": "1",
                "observations": [
                    {
                        "trait": {
                            "kind": "interpretation",
                            "value": "wing_length",
                            "passage": {"kind": "citation", "text": "wing"},
                        },
                        "verbatimScientificName": {"kind": "citation", "text": "Mus musculus"},
                        "verbatimTraitValue": {"kind": "citation", "text": "12.5"},
                        "verbatimTraitUnit": {"kind": "citation", "text": "g"},
                    }
                ],
            }
        )


def test_the_row_schema_rejects_a_sex_abbreviation_and_an_exponent() -> None:
    with pytest.raises(ValidationError):
        _row(sex="M")
    with pytest.raises(ValidationError):
        _row(traitValue="1.5e2")


def test_an_unknown_unit_is_a_drop_reason_without_changing_the_observation() -> None:
    observation = _observation(verbatimTraitUnit=_cited("grams", start=25))
    before = observation.model_dump()

    verdict = assess(observation)

    assert observation.model_dump() == before
    assert isinstance(verdict, Dropped)
    assert verdict.drop == "unit_unknown"


def test_a_negative_age_is_parsed_and_reversed_bounds_drop_the_row() -> None:
    observation = _observation(
        age=_cited("-1", located=False),
        measurementValue_min=_cited("4", start=40),
        measurementValue_max=_cited("2", start=50),
    )

    verdict = assess(observation)
    rows = to_ets(_artifact(observation, _observation()), references=_DOCUMENT_ID)

    assert isinstance(verdict, Dropped)
    assert verdict.drop == "bounds_reversed"
    assert verdict.age == -1
    assert verdict.measurement_value_min == 4
    assert verdict.measurement_value_max == 2
    assert not any(
        isinstance(warning, NotNumeric) and warning.field == "age" for warning in verdict.warnings
    )
    assert len(rows) == 1
    assert rows[0].verbatimTraitUnit == "kg"


def test_an_empty_document_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="references is empty"):
        to_ets(_artifact(_observation()), references="")
