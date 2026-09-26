import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from bioparser.extract.extract import ExtractionFailedError, extract_observations
from bioparser.logging_config import error_fields, log_context, stopwatch
from bioparser.parser.backend.mineru.mapper import artifact_from_middle_json
from bioparser.parser.backend.mineru.parser import (
    MINERU_PARSER_NAME,
    MINERU_PARSER_VERSION,
)
from bioparser.parser.backend.mineru.schema import pipeline_configuration
from bioparser.services.mineru import MinerUError
from bioparser.services.vllm.vllm import VLLMError, VLLMTruncatedError

# TODO: REDIS

logger = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    """Pipeline problems"""


@contextmanager
def _stage(name: str) -> Iterator[dict[str, object]]:
    """Log pipeline stage: duration and outcome."""
    fields: dict[str, object] = {"stage": name}
    elapsed_ms = stopwatch()
    try:
        yield fields
    except Exception as exc:
        fields |= {"outcome": "error", "duration_ms": elapsed_ms()}
        logger.exception("pipeline stage failed", extra=fields | error_fields(exc))
        raise
    fields |= {"outcome": "ok", "duration_ms": elapsed_ms()}
    logger.info("pipeline stage completed", extra=fields)


# REDIS queue worker calls
async def run_pipeline(
    *,
    mineru: Any,
    vllm: Any,
    checksum: str,
    content: bytes,
    char_budget: int,
    max_tokens: int,
) -> dict[str, Any]:
    # Every record logged inside, from any logger, carries the checksum
    with log_context(checksum=checksum):
        try:
            with _stage("parse"):
                middle = await mineru.parse(filename=f"{checksum}.pdf", content=content)
        except MinerUError as exc:
            raise PipelineError("Parsing failed") from exc

        try:
            with _stage("map") as fields:
                artifact = artifact_from_middle_json(
                    middle,
                    checksum=checksum,
                    parser_name=MINERU_PARSER_NAME,
                    parser_version=MINERU_PARSER_VERSION,
                    configuration=pipeline_configuration(),
                )
                fields["pages"] = len(artifact.pages)
        except ValueError as exc:
            # The mapper converts its own ValidationError to ValueError
            raise PipelineError("Parser output could not be mapped") from exc

        try:
            with _stage("extract") as fields:
                report = await extract_observations(
                    vllm, artifact, char_budget=char_budget, max_tokens=max_tokens
                )
                fields.update(
                    blocks_sent=report.blocks_used,
                    prompt_characters=report.prompt_characters,
                    truncated=report.truncated,
                    observations=len(report.observations),
                    ungrounded_observations=report.ungrounded,
                    unit_mismatched_observations=report.unit_mismatched,
                )
        except VLLMTruncatedError as exc:
            raise PipelineError("Extraction truncated") from exc
        except ExtractionFailedError as exc:
            raise PipelineError("Extraction did not match the schema") from exc
        except VLLMError as exc:
            raise PipelineError("Extraction backend unavailable") from exc

    return {
        "checksum": checksum,
        "pages": len(artifact.pages),
        "blocks_sent": report.blocks_used,
        "prompt_characters": report.prompt_characters,
        "truncated": report.truncated,
        "ungrounded_observations": report.ungrounded,
        "unit_mismatched_observations": report.unit_mismatched,
        "observations": [o.payload() for o in report.observations],
    }
