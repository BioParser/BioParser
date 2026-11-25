from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from stubs import StubVLLMService

from bioparser.extract.backend.default.extractor import DefaultExtractor
from bioparser.extract.backend.factory import get_extractor
from bioparser.extract.errors import ExtractorBackendUnavailableError
from bioparser.extractor_names import DEFAULT_EXTRACTOR_NAME, EXTRACTOR_VERSIONS, EXTRACTORS
from bioparser.jobs import JobState
from bioparser.services.vllm import VLLMService


def _parsed_job() -> JobState:
    return JobState(
        job_id=UUID("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e34"),
        status="parsed",
        parser="default",
        extractor=DEFAULT_EXTRACTOR_NAME,
        document_checksum="a" * 64,
        parse_result_ref="pdf/" + ("a" * 64) + "/parsed/default/3",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_default_is_the_registered_extractor() -> None:
    extractor = get_extractor(
        DEFAULT_EXTRACTOR_NAME,
        vllm=cast(VLLMService, StubVLLMService()),
    )

    job = _parsed_job()
    assert isinstance(extractor, DefaultExtractor)
    assert EXTRACTOR_VERSIONS[DEFAULT_EXTRACTOR_NAME] == "2"
    assert extractor.artifact_path_segments(job) == [
        DEFAULT_EXTRACTOR_NAME,
        EXTRACTOR_VERSIONS[DEFAULT_EXTRACTOR_NAME],
        str(job.job_id),
    ]


@pytest.mark.parametrize("name", EXTRACTORS)
def test_every_versioned_extractor_can_be_built(name: str) -> None:
    extractor = get_extractor(
        name,
        vllm=cast(VLLMService, StubVLLMService()),
    )

    assert callable(extractor.extract)


def test_unknown_extractor_name_is_unavailable() -> None:
    with pytest.raises(ExtractorBackendUnavailableError, match="other"):
        get_extractor(
            "other",
            vllm=cast(VLLMService, StubVLLMService()),
        )
