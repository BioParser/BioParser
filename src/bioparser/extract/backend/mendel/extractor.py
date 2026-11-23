from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from bioparser.extract.backend.mendel.disambiguate import (
    AmbiguousField,
    QuoteOption,
    Source,
    ambiguous_fields,
    chosen_choices,
    disambiguation_prompt,
)
from bioparser.extract.backend.mendel.locate import locate_in_page
from bioparser.extract.backend.mendel.schema import ModelObservation, parse_measurements
from bioparser.extract.errors import ExtractionFailedError, ExtractorProcessError
from bioparser.extract.models import ExtractionArtifact, Observation
from bioparser.extractor_names import EXTRACTOR_VERSIONS, MENDEL_EXTRACTOR_NAME
from bioparser.parser.models import BlockRole, ParserArtifact, TextContent
from bioparser.services.vllm import VLLMService
from bioparser.services.vllm.vllm import (
    VLLMContextLengthError,
    VLLMError,
    VLLMRequestRejectedError,
    VLLMTruncatedError,
)

LOGGER = logging.getLogger(__name__)

# Dropping references to cut tokens
SKIP_ROLES = frozenset({BlockRole.REFERENCE, BlockRole.HEADER, BlockRole.FOOTER})

SYSTEM_PROMPT = """You extract ecological trait measurements printed in the passage.

One measurement is one organism, the trait that was measured, and the number
reported for that organism. It is one ETS observation.

Write one measurement with these lines. Leave a line out when the passage
does not print it.
verbatimScientificName: the subject the measurement is about, as printed.
A scientific name is preferred when the passage prints one. A group or
another printed subject is used when it does not. Leave this line out when
the passage does not name a subject.
verbatimTraitName: the property that was measured, as printed.
verbatimTraitValue: the measured number as printed, without a spread and
without a bound.
verbatimTraitUnit: the physical unit as printed. A statistical phrase is not
a unit.
dispersion: the printed spread around the value, the number only.
method: the printed phrase for how the number was summarized.
min: the printed lower bound when the paper gives a range or a greater-than
and no single measured number.
max: the printed upper bound when the paper gives a range or a less-than and
no single measured number.

A blank line separates measurements.
Copy the words the passage prints.
Skip a number that is not a measurement of an organism's trait. That includes
a threshold used to split groups, a count of how often something occurred,
a citation, a year, a figure number, and a sample size.
When the passage reports no measurement, write nothing.
Write only the measurement lines. Do not add a title, a note, or an explanation."""

DISAMBIGUATION_PROMPT = """The passage is the same one you just read.
A reported string occurs more than once.
Reply with the number of the occurrence you meant.
Write nothing else."""


@dataclass(frozen=True, slots=True)
class ModelResult:
    """Measurements located from one paper."""

    observations: list[Observation]


def measurement_pages(artifact: ParserArtifact) -> list[list[Source]]:
    """One entry per page that contains a measurement, in reading order.

    References, headers, footers, and tables are omitted. A page is sent when
    one of its blocks has a digit and a letter. The other text on that page is
    included, so the model sees the sentences around the numbers. A block that
    is only digits, such as a page number, is omitted.
    """
    pages: list[list[Source]] = []
    for page in artifact.pages:
        blocks: list[Source] = []
        has_measurement = False
        for block in page.blocks:
            if block.role in SKIP_ROLES:
                continue
            if not isinstance(block.content, TextContent):
                continue
            text = block.content.text.strip()
            if not text or not any(char.isalpha() for char in text):
                continue
            if any(char.isdigit() for char in text):
                has_measurement = True
            blocks.append((block.block_id, text, 0))
        if has_measurement:
            pages.append(blocks)
    return pages


async def prompt_pieces(
    vllm: VLLMService, blocks: Sequence[Source]
) -> list[list[tuple[str, str, int]]]:
    """The page text, cut into pieces that each fit in the context window.

    ``fit_prefix`` takes as many leading blocks as fit. A block that does not
    fit on its own is split on a space, and the pieces keep their place in the
    original block so a citation can still name that span.
    """
    remaining = _placed(blocks)
    pieces: list[list[tuple[str, str, int]]] = []
    while remaining:
        kept = await vllm.fit_prefix(
            [text for _, text, _origin in remaining],
            system_prompt=SYSTEM_PROMPT,
            separator="\n\n",
        )
        if kept > 0:
            pieces.append(remaining[:kept])
            remaining = remaining[kept:]
            continue
        block_id, text, origin = remaining[0]
        split = _split_once(text)
        if split is None:
            raise VLLMContextLengthError("the prompt does not fit in the context window")
        left, right, right_at = split
        remaining = [
            (block_id, left, origin),
            (block_id, right, origin + right_at),
            *remaining[1:],
        ]
    return pieces


async def extract_observations(
    vllm: VLLMService,
    artifact: ParserArtifact,
) -> ModelResult:
    """One model call per piece of a page. Each ambiguous row is a second call."""
    found: list[Observation] = []
    completed = 0
    truncated = 0
    for blocks in measurement_pages(artifact):
        for piece in await prompt_pieces(vllm, blocks):
            text = "\n\n".join(block_text for _, block_text, _origin in piece)
            try:
                raw = await vllm.generate(text, system_prompt=SYSTEM_PROMPT)
            except VLLMTruncatedError:
                truncated += 1
                LOGGER.warning("extraction skipped a piece that ran to the token limit")
                continue
            completed += 1
            parsed = parse_measurements(raw)
            pending = ambiguous_fields(parsed, piece)
            choices = await _choose(vllm, text, parsed, pending)
            found.extend(locate_in_page(parsed, piece, choices))
    if completed == 0 and truncated:
        raise VLLMTruncatedError("generation hit the token limit")
    return ModelResult(observations=found)


async def _choose(
    vllm: VLLMService,
    passage: str,
    observations: list[ModelObservation],
    pending: list[AmbiguousField],
) -> Mapping[tuple[int, str], QuoteOption]:
    """One second call per row, each starting from the same passage."""
    rows: dict[int, list[AmbiguousField]] = {}
    for field in pending:
        rows.setdefault(field.observation_index, []).append(field)
    chosen: dict[tuple[int, str], QuoteOption] = {}
    for index, fields in rows.items():
        try:
            reply = await vllm.generate(
                disambiguation_prompt(passage, observations[index], fields),
                system_prompt=DISAMBIGUATION_PROMPT,
            )
        except VLLMContextLengthError:
            LOGGER.warning("extraction skipped a disambiguation that does not fit")
            continue
        except VLLMTruncatedError:
            LOGGER.warning("extraction skipped a disambiguation that ran to the token limit")
            continue
        chosen.update(chosen_choices(reply, fields))
    return chosen


def _split_once(text: str) -> tuple[str, str, int] | None:
    """Two smaller pieces, and where the second starts in ``text``."""
    if " " not in text.strip():
        return None
    mid = len(text) // 2
    cut = text.rfind(" ", 0, mid + 1)
    if cut <= 0:
        cut = text.find(" ")
    if cut <= 0 or cut >= len(text) - 1:
        return None
    return text[:cut], text[cut + 1 :], cut + 1


def _placed(blocks: Sequence[Source]) -> list[tuple[str, str, int]]:
    placed: list[tuple[str, str, int]] = []
    for block in blocks:
        if len(block) == 2:
            placed.append((block[0], block[1], 0))
        else:
            placed.append(block)
    return placed


@dataclass(frozen=True, slots=True)
class MendelExtractor:
    """The extractor registered as ``mendel``."""

    vllm: VLLMService

    async def extract(self, artifact: ParserArtifact) -> ExtractionArtifact:
        try:
            result = await extract_observations(self.vllm, artifact)
            return ExtractionArtifact(
                extractor=MENDEL_EXTRACTOR_NAME,
                extractor_version=EXTRACTOR_VERSIONS[MENDEL_EXTRACTOR_NAME],
                observations=result.observations,
            )
        except (VLLMTruncatedError, VLLMRequestRejectedError) as exc:
            raise ExtractionFailedError(f"vLLM could not answer: {type(exc).__name__}") from exc
        except VLLMError as exc:
            raise ExtractorProcessError(str(exc)) from exc
