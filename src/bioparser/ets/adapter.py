"""Assemble ETS rows from assessments. The quality rules live in assess."""

from pydantic import BaseModel, ConfigDict, Field

from bioparser.ets.assess import Assessment, assess
from bioparser.ets.models import EtsRow
from bioparser.ets.notices import format_warning
from bioparser.extract.models import ExtractionArtifact
from bioparser.traits.table import mammal_traits


class MappedRow(BaseModel):
    """One kept row and the observation it was built from."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_index: int = Field(ge=0)
    row: EtsRow


class EtsResult(BaseModel):
    """One row for each observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rows: list[MappedRow]
    # The version of the trait list that named these rows.
    trait_list_version: str


def to_ets(artifact: ExtractionArtifact, *, references: str) -> EtsResult:
    """Map one stored artifact to ETS v0.10 rows.

    ``source_index`` is the position in ``artifact.observations``. ``measurementID``
    is that position plus one. The artifact is not modified.
    """
    if references == "":
        raise ValueError("references is empty")
    rows: list[MappedRow] = []
    for index, observation in enumerate(artifact.observations):
        verdict = assess(observation)
        rows.append(
            MappedRow(
                source_index=index,
                row=_row(verdict, references=references, measurement_id=str(len(rows) + 1)),
            )
        )
    return EtsResult(rows=rows, trait_list_version=mammal_traits().version)


def _row(
    verdict: Assessment,
    *,
    references: str,
    measurement_id: str,
) -> EtsRow:
    return EtsRow.model_validate(
        {
            "traitID": verdict.trait_id,
            "traitName": verdict.trait_name,
            "verbatimTraitName": verdict.cited["verbatimTraitName"],
            "verbatimScientificName": verdict.cited["verbatimScientificName"],
            "traitValue": verdict.trait_value,
            "verbatimTraitValue": verdict.cited["verbatimTraitValue"],
            "traitUnit": verdict.trait_unit,
            "verbatimTraitUnit": verdict.cited["verbatimTraitUnit"],
            "taxonRank": verdict.cited["taxonRank"],
            "kingdom": verdict.cited["kingdom"],
            "phylum": verdict.cited["phylum"],
            "class": verdict.cited["class"],
            "order": verdict.cited["order"],
            "family": verdict.cited["family"],
            "genus": verdict.cited["genus"],
            "sex": verdict.sex,
            "lifeStage": verdict.cited["lifeStage"],
            "age": verdict.age,
            "morphotype": verdict.cited["morphotype"],
            "basisOfRecord": verdict.basis_of_record,
            "basisOfRecordDescription": verdict.cited["basisOfRecordDescription"],
            "measurementMethod": verdict.cited["measurementMethod"],
            "measurementResolution": verdict.cited["measurementResolution"],
            "measurementRemarks": verdict.cited["measurementRemarks"],
            "measurementDeterminedBy": verdict.cited["measurementDeterminedBy"],
            "measurementDeterminedDate": verdict.measurement_determined_date,
            "aggregateMeasure": verdict.aggregate_measure,
            "statisticalMethod": verdict.cited["statisticalMethod"],
            "individualCount": verdict.individual_count,
            "dispersion": verdict.dispersion,
            "measurementValue_min": verdict.measurement_value_min,
            "measurementValue_max": verdict.measurement_value_max,
            "measurementAccuracy": verdict.cited["measurementAccuracy"],
            "occurrenceID": verdict.cited["occurrenceID"],
            "eventID": verdict.cited["eventID"],
            "preparations": verdict.cited["preparations"],
            "samplingProtocol": verdict.cited["samplingProtocol"],
            "year": verdict.year,
            "month": verdict.month,
            "day": verdict.day,
            "eventDate": verdict.event_date,
            "locationID": verdict.cited["locationID"],
            "habitat": verdict.cited["habitat"],
            "decimalLongitude": verdict.decimal_longitude,
            "decimalLatitude": verdict.decimal_latitude,
            "elevation": verdict.elevation,
            "geodeticDatum": verdict.cited["geodeticDatum"],
            "verbatimLocality": verdict.cited["verbatimLocality"],
            "country": verdict.cited["country"],
            "countryCode": verdict.country_code,
            "occurrenceRemarks": verdict.cited["occurrenceRemarks"],
            "measurementID": measurement_id,
            "warnings": " | ".join(format_warning(warning) for warning in verdict.warnings),
            "references": references,
        }
    )
