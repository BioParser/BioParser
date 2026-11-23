import asyncio
from typing import cast

import pytest
from stubs import StubVLLMService

from bioparser.extract.backend.mendel.extractor import extract_observations, prompt_pieces
from bioparser.parser.models import (
    PARSER_ARTIFACT_SCHEMA_VERSION,
    BlockKind,
    BlockRole,
    ContentBlock,
    Page,
    ParserArtifact,
    ParserInfo,
    TextContent,
)
from bioparser.services.vllm import VLLMService
from bioparser.services.vllm.vllm import VLLMContextLengthError

QUOTE = "Mus musculus adults averaged 12.5 kg in the sampled population."


def _paper(text: str = QUOTE) -> ParserArtifact:
    return ParserArtifact(
        schema_version=PARSER_ARTIFACT_SCHEMA_VERSION,
        checksum="a" * 64,
        parser=ParserInfo(name="default", version="1", configuration={}),
        pages=[
            Page(
                page_number=1,
                width=100.0,
                height=100.0,
                blocks=[
                    ContentBlock(
                        block_id="b0",
                        order=0,
                        kind=BlockKind.TEXT,
                        role=BlockRole.BODY,
                        content=TextContent(text=text),
                    )
                ],
            )
        ],
    )


def test_a_long_paper_keeps_the_prefix_that_fits() -> None:
    class ShortFit(StubVLLMService):
        async def fit_prefix(
            self,
            parts: list[str],
            *,
            system_prompt: str | None = None,
            separator: str = "\n\n",
            max_tokens: int | None = None,
            enable_thinking: bool = False,
        ) -> int:
            return 1

    pieces = asyncio.run(
        prompt_pieces(cast(VLLMService, ShortFit()), [("b0", QUOTE, 0), ("b1", "later", 0)])
    )

    assert pieces == [[("b0", QUOTE, 0)], [("b1", "later", 0)]]


def test_a_block_that_does_not_fit_is_split_on_a_space() -> None:
    class NoneFit(StubVLLMService):
        async def fit_prefix(
            self,
            parts: list[str],
            *,
            system_prompt: str | None = None,
            separator: str = "\n\n",
            max_tokens: int | None = None,
            enable_thinking: bool = False,
        ) -> int:
            return 1 if len(parts) > 1 or len(parts[0]) < len(QUOTE) else 0

    pieces = asyncio.run(prompt_pieces(cast(VLLMService, NoneFit()), [("b0", QUOTE, 0)]))

    assert len(pieces) >= 2
    shown = " ".join(text for _block_id, text, _origin in pieces[0])
    assert shown in QUOTE
    _block_id, _text, origin = pieces[1][0]
    assert QUOTE[origin : origin + len(_text)] == _text


def test_eligible_text_that_does_not_fit_is_a_context_length_error() -> None:
    class NoneFit(StubVLLMService):
        async def fit_prefix(
            self,
            parts: list[str],
            *,
            system_prompt: str | None = None,
            separator: str = "\n\n",
            max_tokens: int | None = None,
            enable_thinking: bool = False,
        ) -> int:
            return 0

    with pytest.raises(VLLMContextLengthError, match="context window"):
        asyncio.run(prompt_pieces(cast(VLLMService, NoneFit()), [("b0", "12", 0)]))


def test_a_paper_with_no_eligible_text_stays_empty() -> None:
    paper = _paper()
    paper.pages[0].blocks[0] = (
        paper.pages[0].blocks[0].model_copy(update={"role": BlockRole.REFERENCE})
    )

    result = asyncio.run(extract_observations(cast(VLLMService, StubVLLMService()), paper))

    assert result.observations == []
