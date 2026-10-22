import pytest
from pydantic import ValidationError

from bioparser.ets.adapter import to_ets
from bioparser.ets.assess import Dropped, assess
from bioparser.ets.dates import parse_date
from bioparser.ets.models import EtsRow
from bioparser.ets.warnings import NotNumeric
from bioparser.extract.models import (
    Citation,
    Correction,
    ExtractionArtifact,
    Interpretation,
    Observation,
    TextRange,
)

_DOCUMENT_ID = "abc123"


def _cited(text: str, *, start: int = 0) -> Citation:
    return Citation(
        text=text, evidence=[TextRange(block_id="b0", start=start, end=start + len(text))]
    )


def _read(value: str, passage: str, *, start: int) -> Interpretation:
    return Interpretation(value=value, passage=_cited(passage, start=start))


def _observation(**changes: object) -> Observation:
    observation = Observation(
        trait=_read("body_mass", "body mass", start=10),
        verbatimScientificName=_cited("M. musculus", start=0),
        verbatimTraitValue=_cited("12.5", start=20),
        verbatimTraitUnit=_cited("kg", start=25),
        individualCount=_cited("25", start=30),
        measurementValueMin=_cited("1.5", start=40),
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

    result = to_ets(artifact, references=_DOCUMENT_ID)

    assert artifact.model_dump() == before
    assert result.drops == []
    assert len(result.rows) == 1
    assert result.rows[0].source_index == 0
    row = result.rows[0].row
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


def test_a_stored_name_replaces_the_verbatim_citation() -> None:
    observation = _observation(scientificName=_read("Mus musculus", "M. musculus", start=0))

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.scientificName == "Mus musculus"
    assert row.verbatimScientificName == "M. musculus"


def test_each_stored_trait_part_is_used_on_its_own() -> None:
    both = _observation(
        traitValue=_read("12500", "12.5 kg", start=20),
        traitUnit=_read("g", "12.5 kg", start=20),
    )
    value_only = _observation(traitValue=_read("2", "12.5 kg", start=20))
    unit_only = _observation(traitUnit=_read("g", "kg", start=25))
    neither = _observation()

    rows = [
        to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row
        for observation in (both, value_only, unit_only, neither)
    ]

    assert (rows[0].traitValue, rows[0].traitUnit) == ("12500", "g")
    assert (rows[1].traitValue, rows[1].traitUnit) == ("2000", "g")
    assert (rows[2].traitValue, rows[2].traitUnit) == ("12.5", "g")
    assert (rows[3].traitValue, rows[3].traitUnit) == ("12500", "g")
    assert rows[2].verbatimTraitValue == "12.5"
    assert rows[2].verbatimTraitUnit == "kg"


def test_a_correction_supplies_the_column_and_the_citation_stays_stored() -> None:
    observation = _observation(
        lifeStage=Correction(
            citation=_cited("juv.", start=50),
            interpretation=_read("juvenile", "juvenile males", start=50),
        )
    )
    before = observation.lifeStage

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.lifeStage == "juvenile"
    assert observation.lifeStage == before
    assert isinstance(before, Correction)
    assert before.citation.text == "juv."


def test_length_converts_to_millimetres() -> None:
    observation = _observation(
        trait=_read("tail_length", "tail", start=10),
        verbatimTraitValue=_cited("2", start=20),
        verbatimTraitUnit=_cited("cm", start=25),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.traitValue == "20"
    assert row.traitUnit == "mm"
    assert row.traitName is None


def test_basis_and_aggregate_are_read_from_a_passage() -> None:
    observation = _observation(
        basisOfRecord=_read("FossilSpecimen", "fossil", start=50),
        aggregateMeasure=_read("TRUE", "mean of 12", start=60),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.basisOfRecord == "FossilSpecimen"
    assert row.aggregateMeasure is True


def test_a_vocabulary_miss_leaves_the_column_empty() -> None:
    observation = _observation(
        sex=_cited("M", start=70),
        basisOfRecord=_read("fossil", "fossil", start=50),
        aggregateMeasure=_read("yes", "mean of 12", start=60),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.sex is None
    assert row.basisOfRecord is None
    assert row.aggregateMeasure is None
    assert "not_in_vocabulary:sex" in row.warnings
    assert "not_in_vocabulary:basisOfRecord" in row.warnings
    assert "not_in_vocabulary:aggregateMeasure" in row.warnings


def test_a_paper_can_state_occurrence_place_and_time() -> None:
    observation = _observation(
        occurrenceID=_cited("FMNH 145732", start=70),
        year=_read("1963", "collected in 1963", start=80),
        countryCode=_read("DE", "Germany", start=100),
        eventDate=_cited("8 Mar 1963", start=110),
        measurementDeterminedDate=_cited("March 8, 1963", start=140),
        month=_cited("13", start=130),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.occurrenceID == "FMNH 145732"
    assert row.year == 1963
    assert row.countryCode == "DE"
    assert row.eventDate == "1963-03-08"
    assert row.measurementDeterminedDate == "1963-03-08"
    assert row.month is None
    assert "not_numeric:month" in row.warnings


def test_a_numeric_date_is_kept_when_one_number_cannot_be_a_month() -> None:
    assert parse_date("13/03/1963") == "1963-03-13"
    assert parse_date("03/13/1963") == "1963-03-13"
    assert parse_date("08/03/1963") is None


def test_an_empty_observation_list_returns_no_rows() -> None:
    result = to_ets(_artifact(), references=_DOCUMENT_ID)

    assert result.rows == []
    assert result.drops == []


def test_a_unit_that_does_not_belong_to_the_trait_drops_that_row() -> None:
    bad = _observation(verbatimTraitUnit=_cited("mm", start=25))
    good = _observation()
    before = bad.model_dump()

    verdict = assess(bad)
    result = to_ets(_artifact(bad, good), references=_DOCUMENT_ID)

    assert bad.model_dump() == before
    assert isinstance(verdict, Dropped)
    assert verdict.drop == "unit_not_for_trait"
    assert [(drop.source_index, drop.reason) for drop in result.drops] == [
        (0, "unit_not_for_trait")
    ]
    assert result.rows[0].source_index == 1
    assert result.rows[0].row.verbatimTraitUnit == "kg"
    assert result.rows[0].row.traitValue == "12500"
    assert result.rows[0].row.measurementID == "1"


def test_an_unknown_trait_drops_the_row() -> None:
    observation = _observation(trait=_read("wing_length", "wing", start=10))

    verdict = assess(observation)

    assert isinstance(verdict, Dropped)
    assert verdict.drop == "trait_unknown"


def test_a_citation_without_a_range_is_rejected() -> None:
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
                            "value": "body_mass",
                            "passage": {"kind": "citation", "text": "body mass"},
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
        age=_cited("-1", start=60),
        measurementValueMin=_cited("4", start=40),
        measurementValueMax=_cited("2", start=50),
    )

    verdict = assess(observation)
    result = to_ets(_artifact(observation, _observation()), references=_DOCUMENT_ID)

    assert isinstance(verdict, Dropped)
    assert verdict.drop == "bounds_reversed"
    assert verdict.age == -1
    assert verdict.measurement_value_min == 4
    assert verdict.measurement_value_max == 2
    assert not any(
        isinstance(warning, NotNumeric) and warning.field == "age" for warning in verdict.warnings
    )
    assert result.drops[0].source_index == 0
    assert result.rows[0].source_index == 1
    assert result.rows[0].row.verbatimTraitUnit == "kg"


def test_an_empty_document_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="references is empty"):
        to_ets(_artifact(_observation()), references="")
