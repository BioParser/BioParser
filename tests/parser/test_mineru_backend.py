import json
from pathlib import Path

from bioparser.parser.backend.mineru.mapper import artifact_from_middle_json
from bioparser.parser.backend.mineru.parser import MINERU_PARSER_NAME, MINERU_PARSER_VERSION
from bioparser.parser.backend.mineru.schema import MINERU_PIPELINE_VERSION
from bioparser.parser.models import BlockKind, BlockRole

FIXTURE = Path(__file__).parent / "fixtures" / "mineru-middle.json"
CHECKSUM = "a" * 64


def _text_line(text: str, bbox: list[float], *, cross_page: bool = False) -> dict:
    span: dict = {"bbox": bbox, "content": text, "type": "text"}
    if cross_page:
        span["cross_page"] = True
    return {"bbox": bbox, "spans": [span]}


def _two_page_middle(para_blocks_p0: list, para_blocks_p1: list) -> dict:
    return {
        "_backend": "pipeline",
        "_version_name": MINERU_PIPELINE_VERSION,
        "pdf_info": [
            {
                "page_idx": 0,
                "page_size": [612.0, 792.0],
                "para_blocks": para_blocks_p0,
                "discarded_blocks": [],
            },
            {
                "page_idx": 1,
                "page_size": [612.0, 792.0],
                "para_blocks": para_blocks_p1,
                "discarded_blocks": [],
            },
        ],
    }


def _from_middle(middle: dict):
    return artifact_from_middle_json(
        middle,
        checksum=CHECKSUM,
        parser_name=MINERU_PARSER_NAME,
        parser_version=MINERU_PARSER_VERSION,
        configuration={"backend": "pipeline"},
    )


def test_real_dump_maps_title_levels() -> None:
    middle = json.loads(FIXTURE.read_text(encoding="utf-8"))
    artifact = _from_middle(middle)
    assert [page.page_number for page in artifact.pages] == [1, 2]
    title = next(block for block in artifact.pages[0].blocks if block.role == BlockRole.TITLE)
    assert title.content.heading_level == 1
    assert "Broadening the scope of PLOS Biology" in title.content.text
    headings = [
        block for page in artifact.pages for block in page.blocks if block.role == BlockRole.HEADING
    ]
    assert [block.content.heading_level for block in headings] == [2, 2]


def test_image_leaves_are_skipped() -> None:
    middle = _two_page_middle(
        [
            {
                "type": "text",
                "bbox": [50, 80, 200, 100],
                "lines": [_text_line("keep", [50, 80, 200, 100])],
            },
            {
                "type": "image",
                "bbox": [50, 200, 100, 250],
                "blocks": [
                    {
                        "type": "image_body",
                        "bbox": [50, 200, 100, 250],
                        "lines": [
                            {
                                "bbox": [50, 200, 100, 250],
                                "spans": [
                                    {
                                        "type": "image",
                                        "bbox": [50, 200, 100, 250],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        ],
        [],
    )
    page = _from_middle(middle).pages[0]
    assert [block.content.text for block in page.blocks if block.content.type == "text"] == ["keep"]


def test_lines_join_with_space_and_keep_span_offsets() -> None:
    middle = _two_page_middle(
        [
            {
                "type": "text",
                "bbox": [72, 110, 400, 152],
                "lines": [
                    _text_line("signifi-", [72, 110, 200, 130]),
                    _text_line("cant result", [72, 132, 180, 152]),
                ],
            }
        ],
        [],
    )
    body = _from_middle(middle).pages[0].blocks[0]
    assert body.content.type == "text"
    assert body.content.text == "signifi- cant result"
    assert body.continuation_of is None
    assert body.content.text[body.content.spans[0].start : body.content.spans[0].end] == "signifi-"
    assert body.content.text[body.content.spans[1].start : body.content.spans[1].end] == (
        "cant result"
    )


def test_ref_text_maps_to_reference() -> None:
    middle = _two_page_middle(
        [
            {
                "type": "ref_text",
                "bbox": [50, 700, 540, 740],
                "lines": [_text_line("1. Kang et al.", [50, 700, 540, 720])],
            }
        ],
        [],
    )
    block = _from_middle(middle).pages[0].blocks[0]
    assert block.role == BlockRole.REFERENCE
    assert block.content.text == "1. Kang et al."


def test_table_body_html_maps_to_table_content() -> None:
    middle = _two_page_middle(
        [
            {
                "type": "table",
                "bbox": [72, 200, 540, 400],
                "blocks": [
                    {
                        "type": "table_caption",
                        "bbox": [72, 180, 400, 196],
                        "lines": [_text_line("Table 1. Traits.", [72, 180, 400, 196])],
                    },
                    {
                        "type": "table_body",
                        "bbox": [72, 200, 540, 400],
                        "lines": [
                            {
                                "bbox": [72, 200, 540, 400],
                                "spans": [
                                    {
                                        "type": "table",
                                        "bbox": [72, 200, 540, 400],
                                        "html": (
                                            "<table><tr><td>alpha</td><td>beta</td></tr>"
                                            '<tr><td colspan="2">gamma</td></tr></table>'
                                        ),
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ],
        [],
    )
    page = _from_middle(middle).pages[0]
    assert page.blocks[0].role == BlockRole.CAPTION
    table = page.blocks[1]
    assert table.kind == BlockKind.TABLE
    cells = {(cell.row, cell.column): cell for cell in table.content.cells}
    assert cells[(0, 0)].text == "alpha"
    assert cells[(0, 1)].text == "beta"
    assert cells[(1, 0)].text == "gamma"
    assert cells[(1, 0)].colspan == 2


def test_cross_page_lines_become_next_page_block() -> None:
    middle = _two_page_middle(
        [
            {
                "type": "text",
                "bbox": [50, 700, 250, 740],
                "lines": [
                    _text_line("end of page one", [50, 700, 250, 720]),
                    _text_line("start of page two", [50, 80, 250, 100], cross_page=True),
                ],
            }
        ],
        [],
    )
    artifact = _from_middle(middle)
    first = artifact.pages[0].blocks[0]
    second = artifact.pages[1].blocks[0]
    assert first.content.text == "end of page one"
    assert second.content.text == "start of page two"
    assert first.continuation_of is None
    assert second.continuation_of == first.block_id


def test_consecutive_cross_page_lines_stay_on_one_next_page() -> None:
    middle = _two_page_middle(
        [
            {
                "type": "text",
                "bbox": [50, 80, 250, 740],
                "lines": [
                    _text_line("end of page one", [50, 700, 250, 720]),
                    _text_line("first next-page line", [50, 80, 250, 100], cross_page=True),
                    _text_line("second next-page line", [50, 102, 250, 122], cross_page=True),
                ],
            }
        ],
        [],
    )
    artifact = _from_middle(middle)
    assert artifact.pages[0].blocks[0].content.text == "end of page one"
    cont = artifact.pages[1].blocks[0]
    assert cont.content.text == "first next-page line second next-page line"
    assert cont.continuation_of == artifact.pages[0].blocks[0].block_id
    assert len(artifact.pages[1].blocks) == 1


def test_column_jump_splits_into_two_boxes() -> None:
    middle = _two_page_middle(
        [
            {
                "type": "text",
                "bbox": [50, 80, 550, 720],
                "lines": [
                    _text_line("left column tail", [50, 700, 250, 720]),
                    _text_line("right column head", [320, 80, 550, 100]),
                ],
            }
        ],
        [],
    )
    left, right = _from_middle(middle).pages[0].blocks
    assert [left.content.text, right.content.text] == ["left column tail", "right column head"]
    assert left.continuation_of is None
    assert right.continuation_of == left.block_id
    assert left.bbox is not None and right.bbox is not None
    assert left.bbox.x1 < right.bbox.x0
    assert right.bbox.y1 < left.bbox.y0
