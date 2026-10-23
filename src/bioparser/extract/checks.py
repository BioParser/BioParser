"""The check an extraction artifact passes before it is used. It holds for every extractor.

The extractor worker runs it before it stores an artifact, and ``POST /extract`` before it
returns one, so both give the same result for the same parser artifact.
"""

from __future__ import annotations

from pydantic import ValidationError

from bioparser.extract.citations import reject_mismatched_ranges
from bioparser.extract.errors import ExtractionFailedError
from bioparser.extract.models import ExtractionArtifact
from bioparser.parser.models import ParserArtifact


def checked_artifact(
    returned: ExtractionArtifact,
    parser_artifact: ParserArtifact,
    *,
    extractor: str,
    version: str | None,
) -> ExtractionArtifact:
    """``returned``, checked again. Raises ``ExtractionFailedError`` when a check fails.

    The artifact must match the schema, name ``extractor`` and the ``version`` the caller
    resolved for this run (they also fix its storage path), and every range must be the
    slice it names.
    """
    try:
        artifact = ExtractionArtifact.model_validate(returned.model_dump())
    except ValidationError as exc:
        raise ExtractionFailedError("extractor returned an artifact the schema rejects") from exc
    if artifact.extractor != extractor or artifact.extractor_version != version:
        raise ExtractionFailedError("extractor returned an artifact for another extractor")
    reject_mismatched_ranges(artifact.observations, parser_artifact)
    return artifact
