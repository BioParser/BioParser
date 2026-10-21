from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bioparser.extract.schema import (
    ExtractionArtifact,
    Observation,
    StoredObservation,
    to_canonical,
)


@dataclass(frozen=True, slots=True)
class VerifiedObservation:
    """What the model said, plus what we could check about it.

    Kept separate from Observation so the guided-decoding schema stays exactly
    the fields the model must emit.
    """

    observation: Observation
    # quotation should appear in the block the model cited
    grounded: bool
    unit_ok: bool

    def payload(self) -> dict[str, Any]:
        return {
            **self.observation.model_dump(),
            "canonical_value": to_canonical(self.observation.value, self.observation.unit),
            "grounded": self.grounded,
            "unit_ok": self.unit_ok,
        }


@dataclass(frozen=True, slots=True)
class ExtractionReport:
    observations: list[VerifiedObservation]
    ungrounded: int
    unit_mismatched: int
    blocks_used: int
    prompt_characters: int
    truncated: bool


def artifact_from_report(report: ExtractionReport) -> ExtractionArtifact:
    """The stored artifact for one report. Flags stay on the observations."""
    return ExtractionArtifact(
        observations=[
            StoredObservation.model_validate(item.payload()) for item in report.observations
        ],
        ungrounded=report.ungrounded,
        unit_mismatched=report.unit_mismatched,
        blocks_used=report.blocks_used,
        prompt_characters=report.prompt_characters,
        truncated=report.truncated,
    )
