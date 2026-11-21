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
    TextLine,
    boxes_for_range,
)


def _sample_artifact() -> ParserArtifact:
    return ParserArtifact(
        schema_version=PARSER_ARTIFACT_SCHEMA_VERSION,
        checksum="a" * 64,
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
                            lines=[
                                TextLine(
                                    start=0,
                                    end=5,
                                    y0=0.2,
                                    y1=0.3,
                                    x0=[0.10, 0.14, 0.18, 0.22, 0.26],
                                    x1=[0.14, 0.18, 0.22, 0.26, 0.30],
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


def test_glyph_edges_are_stored_to_four_decimals() -> None:
    line = TextLine(
        start=0,
        end=2,
        y0=0.123456789,
        y1=0.234567891,
        x0=[0.345678912, None],
        x1=[0.456789123, None],
    )
    stored = line.model_dump()
    assert stored["y0"] == round(0.123456789, 4)
    assert stored["x0"] == [round(0.345678912, 4), None]
    assert stored["x1"][0] == round(0.456789123, 4)
    dumped = line.model_dump_json()
    assert "0.123456789" not in dumped
    assert "0.345678912" not in dumped


def test_a_mid_word_range_uses_only_its_glyph_edges() -> None:
    content = _sample_artifact().pages[0].blocks[0].content
    assert isinstance(content, TextContent)
    line = content.lines[0]
    boxes = boxes_for_range([line], 2, 4)
    assert len(boxes) == 1
    assert boxes[0].x0 == 0.18
    assert boxes[0].x1 == 0.26


def test_inverted_bbox_is_rejected() -> None:
    with pytest.raises(ValueError, match="x1 must be greater than or equal to x0"):
        BoundingBox(x0=0.5, y0=0.1, x1=0.2, y1=0.2)
