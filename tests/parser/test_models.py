import pytest

from bioparser.parser import ParserArtifact
from bioparser.parser.models import (
    PARSER_ARTIFACT_SCHEMA_VERSION,
    BlockKind,
    BlockRole,
    BoundingBox,
    ContentBlock,
    Page,
    ParserInfo,
    TextContent,
    TextSpan,
)


def _sample_artifact() -> ParserArtifact:
    return ParserArtifact(
        schema_version=PARSER_ARTIFACT_SCHEMA_VERSION,
        checksum="abc",
        parser=ParserInfo(name="default", version="1", configuration={"text": "pdfplumber"}),
        pages=[
            Page(
                page_number=1,
                width=612.0,
                height=792.0,
                blocks=[
                    ContentBlock(
                        block_id="doc-1-p1-b0000",
                        order=0,
                        kind=BlockKind.TEXT,
                        role=BlockRole.BODY,
                        content=TextContent(
                            text="Hello",
                            spans=[
                                TextSpan(
                                    start=0,
                                    end=5,
                                    line=0,
                                    bbox=BoundingBox(x0=0.1, y0=0.2, x1=0.3, y1=0.3),
                                )
                            ],
                        ),
                        bbox=BoundingBox(x0=0.1, y0=0.2, x1=0.8, y1=0.3),
                    )
                ],
            )
        ],
    )


def test_parser_artifact_json_round_trip() -> None:
    artifact = _sample_artifact()
    restored = ParserArtifact.model_validate_json(artifact.model_dump_json())
    assert restored == artifact


def test_continuation_of_round_trip() -> None:
    artifact = _sample_artifact()
    artifact.pages[0].blocks.append(
        ContentBlock(
            block_id="doc-1-p1-b0001",
            order=1,
            kind=BlockKind.TEXT,
            role=BlockRole.BODY,
            content=TextContent(text="world"),
            continuation_of=artifact.pages[0].blocks[0].block_id,
        )
    )
    restored = ParserArtifact.model_validate_json(artifact.model_dump_json())
    assert restored.pages[0].blocks[1].continuation_of == "doc-1-p1-b0000"


def test_inverted_bbox_is_rejected() -> None:
    with pytest.raises(ValueError, match="x1 must be greater than or equal to x0"):
        BoundingBox(x0=0.5, y0=0.1, x1=0.2, y1=0.2)
