from typing import cast

import pytest
from stubs import StubVLLMService

from bioparser.extract.backend.default.extractor import DefaultExtractor
from bioparser.extract.backend.factory import get_extractor
from bioparser.extract.backend.mendel.extractor import MendelExtractor
from bioparser.extract.errors import ExtractorBackendUnavailableError
from bioparser.extractor_names import (
    DEFAULT_EXTRACTOR_NAME,
    EXTRACTOR_VERSIONS,
    EXTRACTORS,
    MENDEL_EXTRACTOR_NAME,
)
from bioparser.services.vllm import VLLMService


def test_default_is_the_registered_extractor() -> None:
    extractor = get_extractor(
        DEFAULT_EXTRACTOR_NAME,
        vllm=cast(VLLMService, StubVLLMService()),
    )

    assert isinstance(extractor, DefaultExtractor)
    assert EXTRACTOR_VERSIONS[DEFAULT_EXTRACTOR_NAME] == "2"


def test_mendel_is_a_registered_extractor() -> None:
    extractor = get_extractor(
        MENDEL_EXTRACTOR_NAME,
        vllm=cast(VLLMService, StubVLLMService()),
    )

    assert isinstance(extractor, MendelExtractor)
    assert EXTRACTOR_VERSIONS[MENDEL_EXTRACTOR_NAME] == "1"


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
