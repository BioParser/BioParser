from typing import Any

from bioparser.extract.extract import ExtractionFailedError, extract_observations
from bioparser.parser.backend.mineru.mapper import artifact_from_middle_json
from bioparser.parser.backend.mineru.parser import (
    MINERU_PARSER_NAME,
    MINERU_PARSER_VERSION,
)
from bioparser.services.mineru import MinerUError, http_configuration
from bioparser.services.vllm.vllm import VLLMTruncatedError

# TODO: add logger
# TODO: REDIS


class PipelineError(RuntimeError):
    """Pipeline problems"""


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
    try:
        middle = await mineru.parse(filename=f"{checksum}.pdf", content=content)
    except MinerUError as exc:
        raise PipelineError("Parsing failed") from exc

    try:
        artifact = artifact_from_middle_json(
            middle,
            checksum=checksum,
            parser_name=MINERU_PARSER_NAME,
            parser_version=MINERU_PARSER_VERSION,
            configuration=http_configuration(),
        )
    except ValueError as exc:
        # The mapper converts its own ValidationError to ValueError
        raise PipelineError("Parser output could not be mapped") from exc

    try:
        report = await extract_observations(
            vllm, artifact, char_budget=char_budget, max_tokens=max_tokens
        )
    except VLLMTruncatedError as exc:
        raise PipelineError("Extraction truncated") from exc
    except ExtractionFailedError as exc:
        raise PipelineError("Extraction did not match the schema") from exc

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
