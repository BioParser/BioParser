import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from bioparser.extract.backend.default.extractor import extract_observations
from bioparser.extract.demo import demo_response
from bioparser.extract.errors import ExtractionFailedError
from bioparser.logging_config import error_fields, log_context, stopwatch
from bioparser.parser.backend.mineru.mapper import artifact_from_middle_json
from bioparser.parser.backend.mineru.schema import parse_configuration
from bioparser.parser_names import MINERU_PARSER_NAME, PARSER_VERSIONS
from bioparser.services.mineru import MinerUError
from bioparser.services.mineru.client import DEFAULT_TIER, MinerUTier
from bioparser.services.vllm.vllm import VLLMError, VLLMTruncatedError

# TODO: REDIS

LOGGER = logging.getLogger(__name__)


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
        LOGGER.exception("pipeline stage failed", extra=fields | error_fields(exc))
        raise
    fields |= {"outcome": "ok", "duration_ms": elapsed_ms()}
    LOGGER.info("pipeline stage completed", extra=fields)


# REDIS queue worker calls
async def run_pipeline(
    *,
    mineru: Any,
    vllm: Any,
    checksum: str,
    content: bytes,
    tier: MinerUTier = DEFAULT_TIER,
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
                    parser_version=PARSER_VERSIONS[MINERU_PARSER_NAME],
                    configuration=parse_configuration(tier),
                )
                fields["pages"] = len(artifact.pages)
        except ValueError as exc:
            # The mapper converts its own ValidationError to ValueError
            raise PipelineError("Parser output could not be mapped") from exc

        try:
            with _stage("extract") as fields:
                result = await extract_observations(
                    vllm, artifact, char_budget=char_budget, max_tokens=max_tokens
                )
                body = demo_response(
                    result.output,
                    result.prompt,
                    checksum=checksum,
                    pages=len(artifact.pages),
                )
                fields.update(
                    blocks_sent=body["blocks_sent"],
                    prompt_characters=body["prompt_characters"],
                    truncated=body["truncated"],
                    observations=len(result.output.observations),
                    ungrounded_observations=body["ungrounded_observations"],
                    unit_mismatched_observations=body["unit_mismatched_observations"],
                )
        except VLLMTruncatedError as exc:
            raise PipelineError("Extraction truncated") from exc
        except ExtractionFailedError as exc:
            raise PipelineError("Extraction did not match the schema") from exc
        except VLLMError as exc:
            raise PipelineError("Extraction backend unavailable") from exc

    return body
