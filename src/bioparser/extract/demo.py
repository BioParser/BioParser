"""The synchronous POST /extract response. This is not the stored artifact."""

from __future__ import annotations

from typing import Any

from bioparser.extract.backend.default.extractor import Prompt
from bioparser.extract.backend.default.schema import TRAIT_UNITS, Extraction, to_canonical


def demo_response(
    output: Extraction,
    prompt: Prompt,
    *,
    checksum: str,
    pages: int,
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    ungrounded = 0
    unit_mismatched = 0
    for observation in output.observations:
        source = prompt.blocks.get(observation.evidence_block_id)
        grounded = source is not None and observation.quotation in source
        unit_ok = observation.unit in TRAIT_UNITS[observation.trait]
        ungrounded += not grounded
        unit_mismatched += not unit_ok
        observations.append(
            {
                **observation.model_dump(),
                "canonical_value": to_canonical(observation.value, observation.unit),
                "grounded": grounded,
                "unit_ok": unit_ok,
            }
        )
    return {
        "checksum": checksum,
        "pages": pages,
        "blocks_sent": len(prompt.blocks),
        "prompt_characters": len(prompt.text),
        "truncated": prompt.truncated,
        "ungrounded_observations": ungrounded,
        "unit_mismatched_observations": unit_mismatched,
        "observations": observations,
    }
