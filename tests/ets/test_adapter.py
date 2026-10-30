import json

import pytest
from pydantic import ValidationError

from bioparser.ets import trait_values
from bioparser.ets.adapter import to_ets
from bioparser.ets.assess import Dropped, Kept, assess
from bioparser.ets.dates import parse_date
from bioparser.ets.models import EtsRow
from bioparser.ets.notices import NotNumeric
from bioparser.ets.traits import load_table, mammal_traits
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
        traitCode=_read("body_mass", "body mass", start=10),
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
        "traitID": "VT:0001259",
        "traitName": "Body mass",
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
    assert dumped["traitName"] == "Body mass"
    assert dumped["traitID"] == "VT:0001259"
    assert dumped["basisOfRecord"] == "literatureData"
    assert dumped["references"] == _DOCUMENT_ID
    assert dumped["measurementID"] == "1"
    assert dumped["occurrenceID"] is None
    assert dumped["individualCount"] == 25
    assert dumped["measurementValue_min"] is None
    assert dumped["dispersion"] == 0
    assert dumped["warnings"] == ""
    assert "evidence" not in dumped
    assert "traitCode" not in dumped
    aliases = {field.alias or name for name, field in EtsRow.model_fields.items()}
    assert set(dumped) == aliases


def test_a_stored_name_replaces_the_verbatim_citation() -> None:
    observation = _observation(scientificName=_read("Mus musculus", "M. musculus", start=0))

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.scientificName == "Mus musculus"
    assert row.verbatimScientificName == "M. musculus"


def test_each_stored_trait_part_is_used_on_its_own() -> None:
    both = _observation(
        traitValue=_read("13", "12.5 kg", start=20),
        traitUnit=_read("kg", "12.5 kg", start=20),
    )
    value_only = _observation(traitValue=_read("2", "12.5 kg", start=20))
    unit_only = _observation(
        verbatimTraitUnit=_cited("kilograms", start=25),
        traitUnit=_read("kg", "kilograms", start=25),
    )
    neither = _observation()

    rows = [
        to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row
        for observation in (both, value_only, unit_only, neither)
    ]

    assert (rows[0].traitValue, rows[0].traitUnit) == ("13000", "g")
    assert (rows[1].traitValue, rows[1].traitUnit) == ("2000", "g")
    assert (rows[2].traitValue, rows[2].traitUnit) == ("12500", "g")
    assert (rows[3].traitValue, rows[3].traitUnit) == ("12500", "g")
    assert rows[2].verbatimTraitValue == "12.5"
    assert rows[2].verbatimTraitUnit == "kilograms"


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
        traitCode=_read("tail_length", "tail", start=10),
        verbatimTraitValue=_cited("2", start=20),
        verbatimTraitUnit=_cited("cm", start=25),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.traitValue == "20"
    assert row.traitUnit == "mm"
    assert row.traitName == "Tail length"
    assert row.traitID == "VT:0002758"


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
    assert row.basisOfRecord == "literatureData"
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

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.traitValue == "8.5"
    assert row.traitUnit == "g"
    assert row.month == 3
    assert row.day == 8
    assert row.individualCount == 3
    assert row.warnings == ""


def test_an_empty_observation_list_returns_no_rows() -> None:
    result = to_ets(_artifact(), references=_DOCUMENT_ID)

    assert result.rows == []
    assert result.drops == []
    assert result.trait_list_version == mammal_traits().version


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
    observation = _observation(traitCode=_read("wing_length", "wing", start=10))

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
                        "traitCode": {
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
    assert verdict.measurement_value_min == 4000
    assert verdict.measurement_value_max == 2000
    assert not any(
        isinstance(warning, NotNumeric) and warning.field == "age" for warning in verdict.warnings
    )
    assert result.drops[0].source_index == 0
    assert result.rows[0].source_index == 1
    assert result.rows[0].row.verbatimTraitUnit == "kg"


def test_a_long_number_does_not_fail_the_other_rows() -> None:
    long_month = _observation(month=_cited("1" * 40, start=130))
    long_value = _observation(verbatimTraitValue=_cited("8" * 40, start=20))

    result = to_ets(_artifact(long_month, long_value, _observation()), references=_DOCUMENT_ID)

    assert result.rows[0].row.month is None
    assert "not_numeric:month" in result.rows[0].row.warnings
    assert [(drop.source_index, drop.reason) for drop in result.drops] == [(1, "value_not_numeric")]
    assert result.rows[1].source_index == 2
    assert result.rows[1].row.traitValue == "12500"


def test_an_impossible_day_is_cleared_and_a_leap_day_is_kept() -> None:
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

    bad = to_ets(_artifact(impossible), references=_DOCUMENT_ID).rows[0].row
    good = to_ets(_artifact(leap), references=_DOCUMENT_ID).rows[0].row

    assert (bad.year, bad.month, bad.day) == (2021, 2, None)
    assert "not_a_date:year,month,day" in bad.warnings
    assert (good.year, good.month, good.day) == (2020, 2, 29)
    assert good.warnings == ""


def test_a_mean_a_standard_deviation_and_a_range_share_the_trait_unit() -> None:
    observation = _observation(
        centralKind=_read("mean", "mean ± SD, range", start=60),
        dispersionKind=_read("standardDeviation", "mean ± SD, range", start=60),
        dispersion=_cited("0.2", start=70),
        measurementValueMin=_cited("10", start=40),
        measurementValueMax=_cited("14", start=50),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.traitValue == "12500"
    assert row.traitUnit == "g"
    assert row.dispersion == 200
    assert row.measurementValue_min == 10000
    assert row.measurementValue_max == 14000
    assert row.statisticalMethod == "mean and standard deviation"
    assert row.warnings == ""


def test_a_variance_converts_with_the_square_of_the_factor() -> None:
    observation = _observation(
        dispersionKind=_read("variance", "variance", start=60),
        dispersion=_cited("0.2", start=70),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.dispersion == 200_000
    assert row.statisticalMethod == "variance"
    assert row.measurementValue_min is None
    assert row.warnings == ""


def test_a_median_a_standard_error_and_an_interval_convert_length() -> None:
    observation = _observation(
        traitCode=_read("tail_length", "tail", start=10),
        verbatimTraitValue=_cited("2", start=20),
        verbatimTraitUnit=_cited("cm", start=25),
        centralKind=_read("median", "median", start=60),
        dispersionKind=_read("standardError", "SE", start=70),
        dispersion=_cited("0.1", start=90),
        measurementValueMin=_cited("1.2", start=40),
        measurementValueMax=_cited("2.4", start=50),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.traitValue == "20"
    assert row.traitUnit == "mm"
    assert row.dispersion == 1
    assert row.measurementValue_min == 12
    assert row.measurementValue_max == 24
    assert row.statisticalMethod == "median and standard error"
    assert row.warnings == ""


def test_a_minimum_converts_with_the_trait() -> None:
    observation = _observation(measurementValueMin=_cited("1.5", start=40))

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.traitValue == "12500"
    assert row.measurementValue_min == 1500
    assert row.measurementValue_max is None
    assert row.warnings == ""


def test_a_canonical_unit_keeps_a_number_that_is_already_converted() -> None:
    observation = _observation(
        verbatimTraitUnit=_cited("g", start=25),
        dispersion=_cited("0.2", start=70),
        measurementValueMin=_cited("10", start=40),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.traitValue == "12.5"
    assert row.traitUnit == "g"
    assert row.dispersion == 0.2
    assert row.measurementValue_min == 10
    assert row.warnings == ""


def test_a_dispersion_without_a_kind_stays_unconverted() -> None:
    observation = _observation(dispersion=_cited("0.2", start=70))

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.traitValue == "12500"
    assert row.dispersion == 0.2
    assert row.statisticalMethod is None
    assert row.warnings == "not_converted:dispersion"


def test_a_dispersion_kind_without_a_number_is_zero() -> None:
    observation = _observation(
        centralKind=_read("mean", "mean", start=60),
        dispersionKind=_read("standardDeviation", "SD", start=70),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.dispersion == 0
    assert row.statisticalMethod == "mean"
    assert row.warnings == "unspecified:dispersion"


def test_an_empty_maximum_stays_empty() -> None:
    observation = _observation(measurementValueMin=_cited("10", start=40))

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.measurementValue_min == 10000
    assert row.measurementValue_max is None
    assert row.statisticalMethod is None
    assert row.dispersion == 0
    assert row.warnings == ""


def test_a_stored_phrase_is_copied_and_the_choices_still_convert() -> None:
    observation = _observation(
        statisticalMethod=_cited("mean ± SD", start=60),
        centralKind=_read("average", "mean ± SD", start=60),
        dispersionKind=_read("standardDeviation", "mean ± SD", start=60),
        dispersion=_cited("0.2", start=70),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.statisticalMethod == "mean ± SD"
    assert row.dispersion == 200
    assert row.warnings == "not_in_vocabulary:statisticalMethod"


def test_an_unknown_statistic_kind_is_ignored() -> None:
    observation = _observation(
        centralKind=_read("average", "average", start=60),
        dispersionKind=_read("sd", "SD", start=70),
        dispersion=_cited("0.2", start=90),
        measurementValueMin=_cited("10", start=40),
        measurementValueMax=_cited("14", start=50),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.dispersion == 0.2
    assert row.measurementValue_min == 10000
    assert row.measurementValue_max == 14000
    assert row.statisticalMethod is None
    assert row.warnings == "not_in_vocabulary:statisticalMethod | not_converted:dispersion"


def test_an_unknown_central_kind_warns_when_a_dispersion_phrase_is_written() -> None:
    observation = _observation(
        centralKind=_read("average", "average", start=60),
        dispersionKind=_read("variance", "variance", start=70),
        dispersion=_cited("0.2", start=90),
    )

    row = to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row

    assert row.statisticalMethod == "variance"
    assert row.dispersion == 200_000
    assert row.warnings == "not_in_vocabulary:statisticalMethod"


def test_an_empty_document_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="references is empty"):
        to_ets(_artifact(_observation()), references="")


def _trait_observation(code: str, value: str, unit: str) -> Observation:
    return _observation(
        traitCode=_read(code, "trait", start=10),
        verbatimTraitValue=_cited(value, start=20),
        verbatimTraitUnit=_cited(unit, start=25),
    )


def _only_row(observation: Observation) -> EtsRow:
    return to_ets(_artifact(observation), references=_DOCUMENT_ID).rows[0].row


def test_a_count_trait_is_kept_without_conversion() -> None:
    row = _only_row(_trait_observation("litter_size", "4", "unitless"))

    assert (row.traitValue, row.traitUnit) == ("4", "individuals")
    assert (row.traitName, row.traitID) == ("Litter size", row.traitID)
    assert row.verbatimTraitUnit == "unitless"


def test_a_count_must_be_a_positive_whole_number() -> None:
    verdict = assess(_trait_observation("litter_size", "3.5", "-"))

    assert isinstance(verdict, Dropped)
    assert verdict.drop == "value_not_numeric"


def test_a_categorical_level_is_kept_with_no_unit() -> None:
    row = _only_row(_trait_observation("activity_cycle", "Nocturnal", "NA"))

    assert row.traitValue == "nocturnal"
    assert row.traitUnit is None
    assert row.verbatimTraitUnit == "NA"
    assert row.traitName == "Activity cycle"


def test_a_level_off_the_list_drops_the_row() -> None:
    verdict = assess(_trait_observation("activity_cycle", "sometimes", "NA"))

    assert isinstance(verdict, Dropped)
    assert verdict.drop == "level_unknown"


def test_two_length_traits_that_share_a_unit_keep_their_own_identifiers() -> None:
    head_body = _only_row(_trait_observation("head_body_length", "10", "cm"))
    total = _only_row(_trait_observation("total_length", "10", "cm"))

    assert head_body.traitValue == total.traitValue == "100"
    assert head_body.traitUnit == total.traitUnit == "mm"
    assert head_body.traitID != total.traitID


def test_time_density_and_energy_units_convert_to_the_stored_unit() -> None:
    assert _only_row(_trait_observation("gestation_length", "2", "years")).traitValue == "730.5"
    assert _only_row(_trait_observation("home_range_size", "50", "ha")).traitValue == "0.5"
    assert (
        _only_row(_trait_observation("population_density", "3", "individuals/ha")).traitValue
        == "300"
    )
    assert _only_row(_trait_observation("basal_metabolic_rate", "2", "W")).traitValue == "7.2"


def test_a_unit_known_elsewhere_but_not_for_this_trait_is_not_for_the_trait() -> None:
    verdict = assess(_trait_observation("gestation_length", "2", "kHz"))

    assert isinstance(verdict, Dropped)
    assert verdict.drop == "unit_not_for_trait"
    unknown = assess(_trait_observation("gestation_length", "2", "furlongs"))
    assert isinstance(unknown, Dropped)
    assert unknown.drop == "unit_unknown"


def test_a_count_with_its_own_unit_keeps_that_unit() -> None:
    row = _only_row(_trait_observation("teat_number", "8", "NA"))

    assert (row.traitValue, row.traitUnit) == ("8", "teats")
    assert row.warnings == ""


def test_a_count_printed_as_a_mean_may_be_a_decimal_but_a_bare_count_may_not() -> None:
    mean = _observation(
        traitCode=_read("litter_size", "litter", start=10),
        verbatimTraitValue=_cited("3.5", start=20),
        verbatimTraitUnit=_cited("unitless", start=25),
        centralKind=_read("mean", "mean", start=60),
    )
    bare = _trait_observation("litter_size", "3.5", "unitless")

    assert _only_row(mean).traitValue == "3.5"
    verdict = assess(bare)
    assert isinstance(verdict, Dropped)
    assert verdict.drop == "value_not_numeric"


def test_a_conventional_conversion_is_a_warning() -> None:
    row = _only_row(_trait_observation("gestation_length", "2", "years"))
    exact = _only_row(_trait_observation("gestation_length", "60", "days"))

    assert row.warnings == "approximate:traitValue"
    assert exact.warnings == ""


def test_a_real_unit_next_to_a_count_or_a_level_is_a_warning() -> None:
    count = _only_row(_trait_observation("litter_size", "4", "g"))
    level = _only_row(_trait_observation("activity_cycle", "nocturnal", "mm"))

    assert count.warnings == "unit_not_expected:traitUnit"
    assert level.warnings == "unit_not_expected:traitUnit"


def test_a_level_with_a_dispersion_or_a_range_is_a_warning_for_each() -> None:
    observation = _observation(
        traitCode=_read("activity_cycle", "activity", start=10),
        verbatimTraitValue=_cited("nocturnal", start=20),
        verbatimTraitUnit=_cited("NA", start=25),
        dispersion=_read("0.4", "0.4", start=70),
        measurementValueMin=_read("1", "1", start=80),
    )

    row = _only_row(observation)

    assert row.warnings == ("not_applicable:dispersion | not_applicable:measurementValue_min")


def test_a_level_does_not_publish_a_dispersion_or_a_range() -> None:
    observation = _observation(
        traitCode=_read("activity_cycle", "activity", start=10),
        verbatimTraitValue=_cited("nocturnal", start=20),
        verbatimTraitUnit=_cited("NA", start=25),
        dispersion=_read("0.4", "0.4", start=70),
        measurementValueMin=_read("2", "2", start=80),
        measurementValueMax=_read("1", "1", start=90),
    )

    verdict = assess(observation)

    assert isinstance(verdict, Kept)
    assert (verdict.dispersion, verdict.measurement_value_min, verdict.measurement_value_max) == (
        0,
        None,
        None,
    )
    row = _only_row(observation)
    assert row.dispersion == 0
    assert row.measurementValue_min is None and row.measurementValue_max is None


def test_a_mean_group_size_may_be_a_decimal() -> None:
    row = _only_row(_trait_observation("population_group_size", "4.2", "individuals"))

    assert (row.traitValue, row.traitUnit) == ("4.2", "animals")


def test_a_deprecated_trait_still_converts_with_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    base = json.loads(mammal_traits().model_dump_json())
    next(trait for trait in base["traits"] if trait["code"] == "body_mass")["status"] = "deprecated"
    table = load_table(json.dumps(base))
    monkeypatch.setattr(trait_values, "mammal_traits", lambda: table)

    row = _only_row(_observation())

    assert row.traitValue == "12500"
    assert row.warnings == "deprecated_trait:traitName"
