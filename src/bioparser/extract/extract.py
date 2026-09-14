from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from bioparser.extract.schema import TRAIT_UNITS, Extraction, Observation, to_canonical
from bioparser.parser.models import BlockRole, ParserArtifact, TextContent
from bioparser.services.vllm import VLLMService

# Dropping references to cut tokens
SKIP_ROLES = frozenset({BlockRole.REFERENCE, BlockRole.HEADER, BlockRole.FOOTER})

# TODO: logger
# TODO: Change SYSTEM_PROMPT for ETS
# TODO: check render_prompt
SYSTEM_PROMPT = """You extract mammal trait measurements from scientific text.

Return only measurements stated explicitly in the text. For each one, set
evidence_block_id to the [block_id] shown immediately before the sentence, and
quotation to the exact substring of that block containing the measurement.
If the text contains no measurements, return an empty observations list."""


class ExtractionFailedError(RuntimeError):
    """The model answered, but there were problems with the schema"""


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


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    text: str
    blocks: dict[str, str]
    # Was it too long for token budget? -> truncated
    truncated: bool


def render_prompt(artifact: ParserArtifact, *, char_budget: int) -> RenderedPrompt:
    parts: list[str] = []
    blocks: dict[str, str] = {}
    used = 0
    truncated = False

    for page in artifact.pages:
        for block in page.blocks:
            if block.role in SKIP_ROLES:
                continue
            if not isinstance(block.content, TextContent):
                continue  # demo: text only. Tables need their own prompt?
            text = block.content.text.strip()
            if not text:
                continue
            rendered = f"[{block.block_id}] {text}"
            cost = len(rendered) + (2 if parts else 0)  # +2 for the joining "\n\n"
            if used + cost > char_budget:
                # maximimize token budget, but blocks get truncated
                # NOTE: could also just stop early here
                truncated = True
                continue
            blocks[block.block_id] = text
            parts.append(rendered)
            used += cost

    return RenderedPrompt(text="\n\n".join(parts), blocks=blocks, truncated=truncated)


async def extract_observations(
    vllm: VLLMService,
    artifact: ParserArtifact,
    *,
    char_budget: int,
    max_tokens: int,
) -> ExtractionReport:
    rendered = render_prompt(artifact, char_budget=char_budget)
    if not rendered.text:
        return ExtractionReport(
            observations=[],
            ungrounded=0,
            unit_mismatched=0,
            blocks_used=0,
            prompt_characters=0,
            truncated=rendered.truncated,
        )

    raw = await vllm.generate_json(
        rendered.text,
        schema=Extraction.model_json_schema(),
        system_prompt=SYSTEM_PROMPT,
        max_tokens=max_tokens,
    )
    try:
        parsed = Extraction.model_validate_json(raw)
    except ValidationError as exc:
        raise ExtractionFailedError("model output did not match the extraction schema") from exc

    verified: list[VerifiedObservation] = []
    ungrounded = 0
    unit_mismatched = 0
    for observation in parsed.observations:
        source = rendered.blocks.get(observation.evidence_block_id)
        grounded = source is not None and observation.quotation in source
        unit_ok = observation.unit in TRAIT_UNITS[observation.trait]
        ungrounded += not grounded
        unit_mismatched += not unit_ok
        # Every observation is returned, flagged.
        verified.append(
            VerifiedObservation(observation=observation, grounded=grounded, unit_ok=unit_ok)
        )

    return ExtractionReport(
        observations=verified,
        ungrounded=ungrounded,
        unit_mismatched=unit_mismatched,
        blocks_used=len(rendered.blocks),
        prompt_characters=len(rendered.text),
        truncated=rendered.truncated,
    )
