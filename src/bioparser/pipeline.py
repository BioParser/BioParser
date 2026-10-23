"""The queued pipeline in one blocking call: parse, then extract. Nothing is stored.

``POST /extract`` runs it in a child process, so the API's event loop never parses a PDF or
validates a parser artifact. The parser and the extractor come from the same factories, with
the same names and settings, as in the workers. A dev request and a queued job of the same
PDF give the same extraction artifact.

Kept outside ``bioparser.api``: importing that package builds the FastAPI app, which a
child process does not need.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from bioparser.extract.backend.factory import get_extractor
from bioparser.extract.errors import (
    ExtractionFailedError,
    ExtractorBackendUnavailableError,
    ExtractorProcessError,
)
from bioparser.extract.report import artifact_from_report
from bioparser.extract.schema import ExtractionArtifact
from bioparser.hashing import sha256_bytes
from bioparser.logging_config import error_fields, log_context, stopwatch
from bioparser.parser.backend.factory import get_parser
from bioparser.parser.errors import (
    ParserBackendUnavailableError,
    ParserProcessError,
    ParserTimeoutError,
    UnsupportedDocumentError,
)
from bioparser.parser.models import ParserArtifact
from bioparser.parser.protocol import parse_pdf_bytes
from bioparser.services.mineru.client import MinerUTier
from bioparser.services.vllm import VLLMService
from bioparser.services.vllm.vllm import VLLMRequestRejectedError, VLLMTruncatedError

LOGGER = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    """The pipeline gave no result. The message is safe to send to the client."""


@dataclass(frozen=True, slots=True)
class PipelineOptions:
    """What a queued job takes from its record and from its workers' settings."""

    parser: str
    extractor: str
    parser_timeout_s: float
    mineru_base_url: str
    mineru_tier: MinerUTier
    char_budget: int
    max_tokens: int


@contextmanager
def _stage(name: str) -> Iterator[dict[str, object]]:
    """Log one pipeline stage: its duration and outcome."""
    fields: dict[str, object] = {"stage": name}
    elapsed_ms = stopwatch()
    try:
        yield fields
    except Exception as exc:
        fields |= {"outcome": "error", "duration_ms": elapsed_ms()}
        LOGGER.exception("pipeline stage failed", extra=fields | error_fields(exc))
        raise
    fields |= {"outcome": "ok", "duration_ms": elapsed_ms()}
    LOGGER.info("pipeline stage completed", extra=fields)


def run_pipeline(pdf: bytes, options: PipelineOptions) -> ExtractionArtifact:
    """Parse ``pdf``, then extract from its parser artifact.

    Blocks for the whole run and does CPU work: call it in a child process.
    """
    # Every record logged inside, from any logger, carries the checksum
    with log_context(checksum=sha256_bytes(pdf)):
        try:
            with _stage("parse") as fields:
                artifact = _parse(pdf, options)
                fields["pages"] = len(artifact.pages)
        except ParserTimeoutError as exc:
            raise PipelineError("Parsing timed out") from exc
        except UnsupportedDocumentError as exc:
            raise PipelineError("The PDF could not be parsed") from exc
        except (ParserBackendUnavailableError, ParserProcessError) as exc:
            raise PipelineError("Parsing failed") from exc

        try:
            with _stage("extract") as fields:
                extraction = asyncio.run(_extract(artifact, options))
                fields.update(
                    blocks_sent=extraction.blocks_used,
                    prompt_characters=extraction.prompt_characters,
                    truncated=extraction.truncated,
                    observations=len(extraction.observations),
                    ungrounded_observations=extraction.ungrounded,
                    unit_mismatched_observations=extraction.unit_mismatched,
                )
        except ExtractionFailedError as exc:
            raise PipelineError(_extraction_failure(exc)) from exc
        except (ExtractorProcessError, ExtractorBackendUnavailableError) as exc:
            raise PipelineError("Extraction backend unavailable") from exc
    return extraction


def _parse(pdf: bytes, options: PipelineOptions) -> ParserArtifact:
    """The parse worker's parser for ``options.parser``, with the same settings."""
    parser = get_parser(
        options.parser,
        timeout_s=options.parser_timeout_s,
        mineru_base_url=options.mineru_base_url,
        mineru_tier=options.mineru_tier,
    )
    return parse_pdf_bytes(parser, pdf)


async def _extract(artifact: ParserArtifact, options: PipelineOptions) -> ExtractionArtifact:
    """The extractor worker's extractor for ``options.extractor``, and the artifact it stores."""
    # One client per run: each asyncio.run() has its own event loop.
    vllm = VLLMService()
    try:
        extractor = get_extractor(
            options.extractor,
            vllm=vllm,
            char_budget=options.char_budget,
            max_tokens=options.max_tokens,
        )
        return artifact_from_report(await extractor.extract(artifact))
    finally:
        await vllm.aclose()


def _extraction_failure(exc: ExtractionFailedError) -> str:
    if isinstance(exc.__cause__, VLLMTruncatedError):
        return "Extraction truncated"
    if isinstance(exc.__cause__, VLLMRequestRejectedError):
        return "Extraction request rejected"
    return "Extraction did not match the schema"
