import pytest
from pydantic import ValidationError

from bioparser.ets.adapter import to_ets
from bioparser.ets.assess import Assessment, assess
from bioparser.ets.dates import parse_date
from bioparser.ets.models import EtsRow
from bioparser.ets.notices import NotNumeric
from bioparser.extract.models import (
    Citation,
    Correction,
    ExtractionArtifact,
    Interpretation,
    Observation,
    TextRange,
)
from bioparser.traits.table import mammal_traits

_DOCUMENT_ID = "abc123"


def _cited(text: str, *, start: int = 0) -> Citation:
    return Citation(
        text=text, evidence=[TextRange(block_id="b0", start=start, end=start + len(text))]
    )


def _read(value: str, passage: str, *, start: int) -> Interpretation:
    return Interpretation(value=value, passage=_cited(passage, start=start))


def _observation(**changes: object) -> Observation:
    observation = Observation(
        traitCode=_read("body_mass", "body mass", start=10),
        verbatimTraitName=_cited("body mass", start=10),
        verbatimScientificName=_cited("M. musculus", start=0),
        verbatimTraitValue=_cited("12.5", start=20),
        verbatimTraitUnit=_cited("kg", start=25),
        individualCount=_cited("25", start=30),
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
        "verbatimScientificName": "Mus musculus",
        "verbatimTraitName": "body mass",
        "verbatimTraitValue": "12.5",
        "verbatimTraitUnit": "kg",
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
    assert result.trait_list_version == mammal_traits().version
    assert len(result.rows) == 1
    assert result.rows[0].source_index == 0
    row = result.rows[0]
    dumped = row.model_dump()
    assert dumped["traitValue"] == "12500"
    assert dumped["traitUnit"] == "g"
    assert dumped["verbatimTraitValue"] == "12.5"
    assert dumped["verbatimTraitUnit"] == "kg"
    assert dumped["scientificName"] is None
    assert dumped["verbatimScientificName"] == "M. musculus"
    assert dumped["taxonID"] is None
    assert dumped["traitName"] == "Body mass"
    assert dumped["traitID"] == "VT:0001259"
    assert dumped["basisOfRecord"] is None
    assert dumped["references"] == _DOCUMENT_ID
    assert dumped["measurementID"] == "1"
    assert dumped["occurrenceID"] is None
    assert dumped["individualCount"] == 25
    assert dumped["measurementValue_min"] is None
    assert dumped["dispersion"] is None
    assert dumped["warnings"] == ""
    assert "evidence" not in dumped
    assert "trait" not in dumped
    aliases = {field.alias or name for name, field in EtsRow.model_fields.items()}
    assert set(dumped) == aliases | {"source_index"}


def test_a_stored_name_stays_in_the_artifact() -> None:
    """A taxonomy lookup publishes scientificName. The extractor's reading is not a column."""
    observation = _observation(scientificName=_read("Mus musculus", "M. musculus", start=0))

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.scientificName is None
    assert row.taxonID is None
    assert row.verbatimScientificName == "M. musculus"


def test_a_stored_reading_is_what_the_list_converts() -> None:
    """The list uses the stored reading, and falls back to the printed text."""
    stored = _observation(
        traitValue=_read("13", "12.5 kg", start=20),
        traitUnit=_read("kg", "12.5 kg", start=20),
    )

    row = to_ets(_artifact(stored), references=_DOCUMENT_ID).rows[0]

    assert row.traitValue == "13000"
    assert row.traitUnit == "g"
    assert row.traitName == "Body mass"
    assert row.traitID == "VT:0001259"
    assert row.verbatimTraitValue == "12.5"
    assert row.verbatimTraitUnit == "kg"


def test_a_correction_supplies_the_column_and_the_citation_stays_stored() -> None:
    observation = _observation(
        lifeStage=Correction(
            citation=_cited("juv.", start=50),
            interpretation=_read("juvenile", "juvenile males", start=50),
        )
    )
    before = observation.lifeStage

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.lifeStage == "juvenile"
    assert observation.lifeStage == before
    assert isinstance(before, Correction)
    assert before.citation.text == "juv."


def test_a_printed_value_is_converted_and_the_verbatim_stays() -> None:
    observation = _observation(
        traitCode=_read("tail_length", "tail", start=10),
        verbatimTraitValue=_cited("2", start=20),
        verbatimTraitUnit=_cited("cm", start=25),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.traitValue == "20"
    assert row.traitUnit == "mm"
    assert row.traitName == "Tail length"
    assert row.traitID == "VT:0002758"
    assert row.verbatimTraitValue == "2"
    assert row.verbatimTraitUnit == "cm"


def test_basis_and_aggregate_are_read_from_a_passage() -> None:
    observation = _observation(
        basisOfRecord=_read("FossilSpecimen", "fossil", start=50),
        aggregateMeasure=_read("TRUE", "mean of 12", start=60),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.basisOfRecord == "FossilSpecimen"
    assert row.aggregateMeasure is True


def test_sex_tries_the_reading_before_the_printed_word() -> None:
    preferred = _observation(
        sex=Correction(
            citation=_cited("males", start=70),
            interpretation=_read("male", "males", start=70),
        )
    )
    fallback = _observation(
        sex=Correction(
            citation=_cited("male", start=70),
            interpretation=_read("M", "male", start=70),
        )
    )

    preferred_row = to_ets(_artifact(preferred), references=_DOCUMENT_ID).rows[0]
    fallback_row = to_ets(_artifact(fallback), references=_DOCUMENT_ID).rows[0]

    assert preferred_row.sex == "male"
    assert preferred_row.warnings == ""
    assert fallback_row.sex == "male"
    assert fallback_row.warnings == ""


def test_a_country_code_is_written_in_uppercase() -> None:
    observation = _observation(countryCode=_cited("de", start=100))

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.countryCode == "DE"
    assert "not_in_vocabulary:countryCode" not in row.warnings


def test_an_occurrence_id_can_be_read_from_a_wider_phrase() -> None:
    observation = _observation(occurrenceID=_read("145732", "FMNH 145732", start=70))

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.occurrenceID == "145732"
    assert row.taxonRank is None


def test_a_vocabulary_miss_leaves_the_column_empty() -> None:
    observation = _observation(
        sex=_cited("M", start=70),
        basisOfRecord=_read("fossil", "fossil", start=50),
        aggregateMeasure=_read("yes", "mean of 12", start=60),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.sex == "M"
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

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.occurrenceID == "FMNH 145732"
    assert row.year == 1963
    assert row.countryCode == "DE"
    assert row.eventDate == "1963-03-08"
    assert row.measurementDeterminedDate == "1963-03-08"
    assert row.month == 13
    assert "out_of_range:month" in row.warnings


def test_a_numeric_date_is_kept_when_one_number_cannot_be_a_month() -> None:
    assert parse_date("13/03/1963") == "1963-03-13"
    assert parse_date("03/13/1963") == "1963-03-13"
    assert parse_date("13.03.1963") == "1963-03-13"
    assert parse_date("03.13.1963") == "1963-03-13"
    assert parse_date("29.02.1964") == "1964-02-29"
    assert parse_date("1963.03.08") == "1963-03-08"
    assert parse_date("08/03/1963") is None
    assert parse_date("08.03.1963") is None
    assert parse_date("13/13/1963") is None
    assert parse_date("31/04/1963") is None
    assert parse_date("29/02/1963") is None
    assert parse_date("29.02.1963") is None
    assert parse_date("8 Mar 63") is None
    assert parse_date("19630308") is None
    assert parse_date("1963-03-08T10:00:00") is None


def test_a_written_date_keeps_a_comma_an_ordinal_and_september() -> None:
    assert parse_date("8 March, 1963") == "1963-03-08"
    assert parse_date("8 Mar, 1963") == "1963-03-08"
    assert parse_date("8. March 1963") == "1963-03-08"
    assert parse_date("8th March 1963") == "1963-03-08"
    assert parse_date("March 8th, 1963") == "1963-03-08"
    assert parse_date("8 Sept 1963") == "1963-09-08"
    assert parse_date("8 Sept. 1963") == "1963-09-08"
    assert parse_date("8 September 1963") == "1963-09-08"
    assert parse_date("5 Jan. 2020") == "2020-01-05"
    assert parse_date("Aug. 5, 2020") == "2020-08-05"
    assert parse_date("8. Mar. 1963") == "1963-03-08"


def test_a_written_date_accepts_prose_and_other_separators() -> None:
    assert parse_date("8th of March 1963") == "1963-03-08"
    assert parse_date("8th of March, 1963") == "1963-03-08"
    assert parse_date("the 8th March 1963") == "1963-03-08"
    assert parse_date("8/Mar/1963") == "1963-03-08"
    assert parse_date("8/March/1963") == "1963-03-08"
    assert parse_date("1963 Mar 8") == "1963-03-08"
    assert parse_date("1963 March 8") == "1963-03-08"
    assert parse_date("1963-Mar-08") == "1963-03-08"
    assert parse_date("August 5. 2020") == "2020-08-05"
    assert parse_date("Aug. 5. 2020") == "2020-08-05"
    assert parse_date("8\u2013Mar\u20131963") == "1963-03-08"
    assert parse_date("  8 Mar 1963  ") == "1963-03-08"
    assert parse_date("March 1963") is None
    assert parse_date("8\u201310 March 1963") is None


def test_a_zero_padded_number_is_normalized() -> None:
    observation = _observation(
        verbatimTraitValue=_cited("08.5", start=20),
        verbatimTraitUnit=_cited("g", start=25),
        month=_cited("03", start=130),
        day=_cited("08", start=140),
        individualCount=_cited("03", start=30),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.traitValue == "8.5"
    assert row.traitUnit == "g"
    assert row.verbatimTraitValue == "08.5"
    assert row.verbatimTraitUnit == "g"
    assert row.month == 3
    assert row.day == 8
    assert row.individualCount == 3
    assert row.warnings == ""


def test_an_empty_observation_list_returns_no_rows() -> None:
    result = to_ets(_artifact(), references=_DOCUMENT_ID)

    assert result.rows == []


def test_a_unit_that_does_not_match_the_trait_is_still_a_row() -> None:
    """The printed unit is copied. A trait list checks it against the trait."""
    odd = _observation(verbatimTraitUnit=_cited("mm", start=25))
    before = odd.model_dump()

    verdict = assess(odd)
    result = to_ets(_artifact(odd, _observation()), references=_DOCUMENT_ID)

    assert odd.model_dump() == before
    assert isinstance(verdict, Assessment)
    assert [row.source_index for row in result.rows] == [0, 1]
    assert result.rows[0].verbatimTraitUnit == "mm"
    assert result.rows[0].measurementID == "1"
    assert result.rows[1].measurementID == "2"


def test_a_measurement_without_a_code_is_a_row() -> None:
    observation = _observation(traitCode=None)

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.verbatimTraitName == "body mass"
    assert row.verbatimTraitValue == "12.5"
    assert row.traitName is None
    assert row.traitID is None
    assert row.traitValue is None
    assert row.traitUnit is None
    assert "unmapped:traitName" in row.warnings


def test_an_unknown_trait_is_still_a_row() -> None:
    observation = _observation(traitCode=_read("wing_length", "wing", start=10))

    verdict = assess(observation)
    result = to_ets(_artifact(observation), references=_DOCUMENT_ID)

    assert isinstance(verdict, Assessment)
    assert result.rows[0].verbatimTraitName == "body mass"
    assert result.rows[0].traitID is None
    assert "unmapped:traitName" in result.rows[0].warnings


def test_a_citation_without_a_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtractionArtifact.model_validate(
            {
                "schema_version": "1",
                "extractor": "default",
                "extractor_version": "1",
                "observations": [
                    {
                        "traitCode": {
                            "kind": "interpretation",
                            "value": "body_mass",
                            "passage": {"kind": "citation", "text": "body mass"},
                        },
                        "verbatimTraitName": {"kind": "citation", "text": "body mass"},
                        "verbatimScientificName": {"kind": "citation", "text": "Mus musculus"},
                        "verbatimTraitValue": {"kind": "citation", "text": "12.5"},
                    }
                ],
            }
        )


def test_the_row_keeps_a_sex_abbreviation_and_rejects_an_exponent() -> None:
    assert _row(sex="M").sex == "M"
    with pytest.raises(ValidationError):
        _row(traitValue="1.5e2")


def test_an_unparsed_unit_stays_printed() -> None:
    observation = _observation(verbatimTraitUnit=_cited("grams", start=25))
    before = observation.model_dump()

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert observation.model_dump() == before
    assert row.verbatimTraitUnit == "grams"
    assert row.traitValue == "12.5"
    assert row.traitUnit == "g"


def test_a_missing_unit_stays_empty() -> None:
    observation = _observation(verbatimTraitUnit=None)

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.verbatimTraitUnit is None
    assert row.traitID is None
    assert "unit_unknown:traitUnit" in row.warnings


def test_a_negative_age_is_parsed_and_reversed_bounds_are_kept() -> None:
    observation = _observation(
        age=_cited("-1", start=60),
        measurementValueMin=_cited("4", start=40),
        measurementValueMax=_cited("2", start=50),
    )

    verdict = assess(observation)
    result = to_ets(_artifact(observation, _observation()), references=_DOCUMENT_ID)

    assert isinstance(verdict, Assessment)
    assert verdict.age == -1
    assert verdict.measurement_value_min == 4
    assert verdict.measurement_value_max == 2
    assert not any(
        isinstance(warning, NotNumeric) and warning.field == "age" for warning in verdict.warnings
    )
    assert result.rows[0].source_index == 0
    assert result.rows[0].measurementValue_min == 4
    assert result.rows[0].measurementValue_max == 2
    assert "bounds_reversed:measurementValue_min,measurementValue_max" in result.rows[0].warnings
    assert result.rows[1].source_index == 1


def test_a_printed_token_stays_when_it_fails_its_check() -> None:
    observation = _observation(
        month=_cited("February", start=130),
        eventDate=_cited("08/03/1963", start=110),
        countryCode=_cited("Germany", start=100),
        decimalLongitude=_cited("200", start=150),
        individualCount=_cited("0", start=30),
        dispersion=_cited("-0.2", start=40),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.month == "February"
    assert row.eventDate == "08/03/1963"
    assert row.countryCode == "Germany"
    assert row.decimalLongitude == 200
    assert row.individualCount == 0
    assert row.dispersion == -0.2
    assert "out_of_range:individualCount" in row.warnings
    assert "out_of_range:dispersion" in row.warnings
    assert "not_numeric:month" in row.warnings
    assert "not_a_date:eventDate" in row.warnings
    assert "out_of_range:decimalLongitude" in row.warnings
    assert "not_in_vocabulary:countryCode" in row.warnings


def test_a_long_number_does_not_fail_the_other_rows() -> None:
    long_month = _observation(month=_cited("1" * 40, start=130))
    long_value = _observation(verbatimTraitValue=_cited("8" * 40, start=20))

    result = to_ets(_artifact(long_month, long_value, _observation()), references=_DOCUMENT_ID)

    assert result.rows[0].month == "1" * 40
    assert "not_numeric:month" in result.rows[0].warnings
    assert result.rows[1].source_index == 1
    assert result.rows[1].verbatimTraitValue == "8" * 40
    assert result.rows[1].traitValue is None
    assert "not_numeric:traitValue" in result.rows[1].warnings
    assert result.rows[2].source_index == 2


def test_an_impossible_day_stays_and_a_leap_day_is_kept() -> None:
    impossible = _observation(
        year=_cited("2021", start=80),
        month=_cited("2", start=90),
        day=_cited("31", start=100),
    )
    leap = _observation(
        year=_cited("2020", start=80),
        month=_cited("2", start=90),
        day=_cited("29", start=100),
    )

    bad = to_ets(_artifact(impossible), references=_DOCUMENT_ID).rows[0]
    good = to_ets(_artifact(leap), references=_DOCUMENT_ID).rows[0]

    assert (bad.year, bad.month, bad.day) == (2021, 2, 31)
    assert "not_a_date:year,month,day" in bad.warnings
    assert (good.year, good.month, good.day) == (2020, 2, 29)
    assert good.warnings == ""


def test_a_range_and_a_dispersion_stay_printed() -> None:
    observation = _observation(
        statisticalMethod=_cited("mean ± SD, range", start=60),
        dispersion=_cited("0.2", start=70),
        measurementValueMin=_cited("10", start=40),
        measurementValueMax=_cited("14", start=50),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.traitValue == "12500"
    assert row.traitUnit == "g"
    assert row.dispersion == 0.2
    assert row.measurementValue_min == 10
    assert row.measurementValue_max == 14
    assert row.statisticalMethod == "mean ± SD, range"
    assert "not_converted:dispersion" in row.warnings
    assert "not_converted:measurementValue_min" in row.warnings
    assert "not_converted:measurementValue_max" in row.warnings


def test_a_missing_method_stays_empty() -> None:
    observation = _observation(dispersion=_cited("0.2", start=70))

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.dispersion == 0.2
    assert row.statisticalMethod is None
    assert row.warnings == "not_converted:dispersion"


def test_a_missing_count_and_dispersion_stay_empty() -> None:
    observation = _observation(individualCount=None)

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.individualCount is None
    assert row.dispersion is None
    assert row.warnings == ""


def test_a_minimum_stays_printed() -> None:
    observation = _observation(measurementValueMin=_cited("1.5", start=40))

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.measurementValue_min == 1.5
    assert row.measurementValue_max is None
    assert row.warnings == "not_converted:measurementValue_min"


def test_an_empty_maximum_stays_empty() -> None:
    observation = _observation(measurementValueMin=_cited("10", start=40))

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.measurementValue_min == 10
    assert row.measurementValue_max is None
    assert row.statisticalMethod is None
    assert row.dispersion is None
    assert row.warnings == "not_converted:measurementValue_min"


def test_a_level_is_standardized_when_it_has_no_spread() -> None:
    observation = _observation(
        traitCode=_read("activity_cycle", "activity", start=10),
        verbatimTraitValue=_cited("nocturnal", start=20),
        verbatimTraitUnit=None,
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.traitName == "Activity cycle"
    assert row.traitID == "BPT:0000050"
    assert row.traitValue == "nocturnal"
    assert row.traitUnit is None
    assert row.warnings == ""


def test_a_level_with_a_spread_stays_the_printed_row() -> None:
    observation = _observation(
        traitCode=_read("activity_cycle", "activity", start=10),
        verbatimTraitValue=_cited("nocturnal", start=20),
        verbatimTraitUnit=None,
        dispersion=_read("0.4", "0.4", start=70),
        measurementValueMin=_read("1", "1", start=80),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0]

    assert row.traitName is None
    assert row.traitID is None
    assert row.traitValue is None
    assert row.traitUnit is None
    assert row.verbatimTraitValue == "nocturnal"
    assert row.dispersion == 0.4
    assert row.measurementValue_min == 1
    assert row.warnings == "not_applicable:dispersion | not_applicable:measurementValue_min"


def test_an_empty_document_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="references is empty"):
        to_ets(_artifact(_observation()), references="")
