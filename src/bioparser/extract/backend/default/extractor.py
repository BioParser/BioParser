from __future__ import annotations

from dataclasses import dataclass

from bioparser.extract.backend.default.locate import cited_observations
from bioparser.extract.backend.default.schema import Extraction
from bioparser.extract.errors import ExtractionFailedError, ExtractorProcessError
from bioparser.extract.models import ExtractionArtifact
from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME, EXTRACTOR_VERSIONS
from bioparser.parser.models import BlockRole, ParserArtifact, TextContent
from bioparser.services.vllm import VLLMService
from bioparser.services.vllm.vllm import (
    VLLMError,
    VLLMRequestRejectedError,
    VLLMSchemaError,
    VLLMTruncatedError,
)

# Dropping references to cut tokens
SKIP_ROLES = frozenset({BlockRole.REFERENCE, BlockRole.HEADER, BlockRole.FOOTER})

# TODO: Change SYSTEM_PROMPT for ETS
SYSTEM_PROMPT = """You extract mammal trait measurements from scientific text.

Return only measurements stated explicitly in the text. For each one, set
evidence_block_id to the [block_id] shown immediately before the sentence, and
quotation to the exact substring of that block containing the measurement.
If the text contains no measurements, return an empty observations list."""


@dataclass(frozen=True, slots=True)
class Prompt:
    """Text sent to the model, and the blocks that text was built from."""

    text: str
    blocks: dict[str, str]


@dataclass(frozen=True, slots=True)
class ModelResult:
    """One model call: the prompt it was shown, and the payload it returned."""

    output: Extraction
    prompt: Prompt


async def render_prompt(artifact: ParserArtifact, *, vllm: VLLMService) -> Prompt:
    """Eligible text blocks, joined for one model call.

    References, headers, and footers are omitted. Tables are omitted.
    """
    eligible: list[tuple[str, str]] = []
    for page in artifact.pages:
        for block in page.blocks:
            if block.role in SKIP_ROLES:
                continue
            if not isinstance(block.content, TextContent):
                continue  # demo: text only. Tables need their own prompt?
            text = block.content.text.strip()
            if not text:
                continue
            eligible.append((block.block_id, text))

    parts = [f"[{block_id}] {text}" for block_id, text in eligible]
    kept_count = await vllm.fit_prefix(parts, system_prompt=SYSTEM_PROMPT)
    # TODO: A finished extractor covers the whole paper or fails. This one keeps
    # the prefix that fits, because the current window rejects most papers.
    kept = eligible[:kept_count]
    return Prompt(
        text="\n\n".join(parts[:kept_count]),
        blocks={block_id: text for block_id, text in kept},
    )


async def extract_observations(
    vllm: VLLMService,
    artifact: ParserArtifact,
) -> ModelResult:
    prompt = await render_prompt(artifact, vllm=vllm)
    if not prompt.text:
        return ModelResult(output=Extraction(), prompt=prompt)

    parsed = await vllm.generate_model(
        prompt.text,
        Extraction,
        system_prompt=SYSTEM_PROMPT,
    )
    return ModelResult(output=parsed, prompt=prompt)


@dataclass(frozen=True, slots=True)
class DefaultExtractor:
    """The extractor registered as ``default``."""

    vllm: VLLMService

    async def extract(self, artifact: ParserArtifact) -> ExtractionArtifact:
        try:
            result = await extract_observations(self.vllm, artifact)
            return ExtractionArtifact(
                extractor=DEFAULT_EXTRACTOR_NAME,
                extractor_version=EXTRACTOR_VERSIONS[DEFAULT_EXTRACTOR_NAME],
                observations=cited_observations(result.output, artifact),
            )
        except VLLMSchemaError as exc:
            raise ExtractionFailedError(str(exc)) from exc
        except (VLLMTruncatedError, VLLMRequestRejectedError) as exc:
            raise ExtractionFailedError(f"vLLM could not answer: {type(exc).__name__}") from exc
        except VLLMError as exc:
            raise ExtractorProcessError(str(exc)) from exc
