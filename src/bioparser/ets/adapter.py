"""Assemble ETS rows from assessments. The quality rules live in assess."""

from bioparser.ets.assess import Dropped, Kept, assess
from bioparser.ets.models import EtsRow
from bioparser.ets.warnings import format_warning
from bioparser.extract.models import ExtractionArtifact


def to_ets(artifact: ExtractionArtifact, *, references: str) -> list[EtsRow]:
    """Map one stored artifact to ETS v0.10 rows.

    A dropped observation is absent. The artifact is not modified.
    """
    if references == "":
        raise ValueError("references is empty")
    rows: list[EtsRow] = []
    for observation in artifact.observations:
        verdict = assess(observation)
        if isinstance(verdict, Dropped):
            continue
        rows.append(_row(verdict, references=references, measurement_id=str(len(rows) + 1)))
    _require_unique_ids(rows)
    return rows


def _require_unique_ids(rows: list[EtsRow]) -> None:
    identifiers = [row.measurementID for row in rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("measurementID values are not unique")


def _row(
    verdict: Kept,
    *,
    references: str,
    measurement_id: str,
) -> EtsRow:
    return EtsRow.model_validate(
        {
            "verbatimTraitName": verdict.cited["verbatimTraitName"],
            "scientificName": verdict.cited["verbatimScientificName"],
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
