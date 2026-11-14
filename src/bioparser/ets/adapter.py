"""Assemble ETS rows from assessments. The quality rules live in assess."""

from pydantic import BaseModel, ConfigDict, Field

from bioparser.ets.assess import Assessment, assess
from bioparser.ets.models import EtsRow
from bioparser.ets.notices import format_warning
from bioparser.extract.models import ExtractionArtifact


class EtsSourceRow(EtsRow):
    """An ETS row plus the observation it was built from.

    ``source_index`` is not an ETS column. It is the position in the artifact's
    observations, so a row can be matched back to the observation that produced it.
    """

    source_index: int = Field(ge=0)


class EtsResult(BaseModel):
    """One row per observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rows: list[EtsSourceRow]


def to_ets(artifact: ExtractionArtifact, *, references: str) -> EtsResult:
    """Map one stored artifact to ETS v0.10 rows.

    ``source_index`` is the position in ``artifact.observations``. ``measurementID``
    counts the rows, starting at 1. The artifact is not modified.
    """
    if references == "":
        raise ValueError("references is empty")
    rows: list[EtsSourceRow] = []
    for index, observation in enumerate(artifact.observations):
        rows.append(
            _row(
                assess(observation),
                references=references,
                measurement_id=str(len(rows) + 1),
                source_index=index,
            )
        )
    return EtsResult(rows=rows)


def _row(
    verdict: Assessment,
    *,
    references: str,
    measurement_id: str,
    source_index: int,
) -> EtsSourceRow:
    return EtsSourceRow.model_validate(
        {
            "verbatimTraitName": verdict.cited["verbatimTraitName"],
            "scientificName": verdict.scientific_name,
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
            "occurrenceID": verdict.occurrence_id,
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
            "source_index": source_index,
        }
    )
