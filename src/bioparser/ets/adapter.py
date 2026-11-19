"""Assemble ETS rows from assessments. The quality rules live in assess."""

from pydantic import BaseModel, ConfigDict

from bioparser.ets.assess import Assessment, assess
from bioparser.ets.models import EtsRow
from bioparser.ets.notices import format_warning
from bioparser.extract.models import ExtractionArtifact


class EtsResult(BaseModel):
    """One row per observation, in the artifact's order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rows: list[EtsRow]


def to_ets(artifact: ExtractionArtifact, *, references: str) -> EtsResult:
    """Map one stored artifact to ETS v0.10 rows.

    ``measurementID`` counts the rows, starting at 1. The row's position is the
    observation's position. The artifact is not modified.
    """
    if references == "":
        raise ValueError("references is empty")
    rows: list[EtsRow] = []
    for observation in artifact.observations:
        rows.append(
            _row(
                assess(observation),
                references=references,
                measurement_id=str(len(rows) + 1),
            )
        )
    return EtsResult(rows=rows)


def _infer_taxon_rank(name: str | None) -> str | None:
    if name is None:
        return None

    normalized = name.strip().lower()
    words = normalized.split()
    word_count = len(words)
    if any(word in {"sp.", "spp.", "cf.", "aff."} for word in words):
        return None

    if "(" in normalized or ")" in normalized:
        return None
    if word_count >= 2 and words[-1].endswith("."):
        return None
    if word_count == 1:
        if normalized.endswith("oidea"):
            return "superfamily"
        if normalized.endswith("idae"):
            return "family"
        if normalized.endswith("inae"):
            return "subfamily"
        if normalized.endswith("ini"):
            return "tribe"
        if normalized.endswith("ina"):
            return "subtribe"
        return "genus"

    if word_count == 2:
        return "species"
    if word_count == 3:
        return "subspecies"
    return None


def _row(
    verdict: Assessment,
    *,
    references: str,
    measurement_id: str,
) -> EtsRow:
    return EtsRow.model_validate(
        {
            "verbatimTraitName": verdict.cited["verbatimTraitName"],
            "scientificName": verdict.scientific_name,
            "verbatimScientificName": verdict.cited["verbatimScientificName"],
            "traitValue": verdict.trait_value,
            "verbatimTraitValue": verdict.cited["verbatimTraitValue"],
            "traitUnit": verdict.trait_unit,
            "verbatimTraitUnit": verdict.cited["verbatimTraitUnit"],
            "taxonRank": _infer_taxon_rank(verdict.cited["verbatimScientificName"]),
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
        }
    )
