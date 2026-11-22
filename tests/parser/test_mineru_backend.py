import json
from pathlib import Path
from typing import Any

import pytest

from bioparser.parser.backend.mineru.mapper import (
    MiddleJsonContractError,
    artifact_from_middle_json,
)
from bioparser.parser.backend.mineru.schema import MINERU_VERSION
from bioparser.parser.backend.mineru.tables import (
    HTML_CELL_MAX_OCCUPIED,
    HTML_CELL_MAX_SPAN,
    _cells_from_html,
)
from bioparser.parser.models import (
    BlockKind,
    BlockRole,
    ContentBlock,
    ParserArtifact,
    TableContent,
    TextContent,
    boxes_for_range,
)
from bioparser.parser_names import MINERU_PARSER_NAME, PARSER_VERSIONS

FIXTURE = Path(__file__).parent / "fixtures" / "mineru-middle.json"
ARTICLE = Path(__file__).parent / "fixtures" / "mineru-middle-1002000.json"
CHECKSUM = "a" * 64


def _spans(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "content": text}]


def _block(kind: str, index: int, text: str, **extra: Any) -> dict[str, Any]:
    return {
        "type": kind,
        "index": index,
        "bbox": [0.1, 0.1, 0.9, 0.2],
        "content": _spans(text),
        **extra,
    }


def _middle(*pages: list[dict[str, Any]]) -> dict[str, Any]:
    """A docvortex.middle document with one 612 x 792 pt page per list of blocks."""
    return {
        "schema": "docvortex.middle",
        "schema_version": "2.0",
        "metadata": {
            "file_suffix": "pdf",
            "producer": {"name": "mineru", "version": MINERU_VERSION},
        },
        "extensions": {
            "docvortex_layout": {
                "version": 1,
                "pages": [
                    {"page_idx": idx, "width_pt": 612.0, "height_pt": 792.0}
                    for idx in range(len(pages))
                ],
            }
        },
        "pages": [{"page_idx": idx, "blocks": blocks} for idx, blocks in enumerate(pages)],
        "is_full_document": True,
    }


def _from_middle(middle: dict[str, Any]) -> ParserArtifact:
    return artifact_from_middle_json(
        middle,
        checksum=CHECKSUM,
        parser_name=MINERU_PARSER_NAME,
        parser_version=PARSER_VERSIONS[MINERU_PARSER_NAME],
        configuration={"tier": "basic"},
    )


def _text(block: ContentBlock) -> TextContent:
    assert isinstance(block.content, TextContent)
    return block.content


def _table(block: ContentBlock) -> TableContent:
    assert isinstance(block.content, TableContent)
    return block.content


def test_real_dump_maps_pages_titles_and_drops_page_furniture() -> None:
    artifact = _from_middle(json.loads(FIXTURE.read_text(encoding="utf-8")))

    assert [(page.page_number, page.width, page.height) for page in artifact.pages] == [
        (1, 612.0, 792.0),
        (2, 612.0, 792.0),
        (3, 612.0, 792.0),
    ]
    blocks = [block for page in artifact.pages for block in page.blocks]
    title = next(block for block in blocks if block.role == BlockRole.TITLE)
    assert _text(title).heading_level == 1
    assert "Broadening the scope of PLOS Biology" in _text(title).text
    headings = [block for block in blocks if block.role == BlockRole.HEADING]
    assert [_text(block).heading_level for block in headings] == [2] * 5
    texts = [_text(block).text for block in blocks]
    assert not any(text.startswith("PLOS Biology | ") or text.endswith(" / 3") for text in texts)
    assert all(block.bbox is not None for block in blocks)


def test_real_article_keeps_continuations_tables_and_exponents() -> None:
    artifact = _from_middle(json.loads(ARTICLE.read_text(encoding="utf-8")))
    where = {
        block.block_id: (page.page_number, block.order)
        for page in artifact.pages
        for block in page.blocks
    }
    links = {
        (page.page_number, block.order): where[block.continuation_of]
        for page in artifact.pages
        for block in page.blocks
        if block.continuation_of is not None
    }

    assert len(links) == 11
    assert links[(1, 20)] == (1, 19)
    assert links[(10, 1)] == (9, 17)  # across the page break
    assert links[(11, 5)] == (11, 4)  # across the column break (MinerU 4.2 finds this one)
    tables = [block for block in artifact.pages[8].blocks if block.kind == BlockKind.TABLE]
    assert [len(_table(block).cells) for block in tables] == [136]
    assert "approximately equal to 10^{9} neurons" in _text(artifact.pages[0].blocks[4]).text


def test_superscripts_and_subscripts_stay_apart_from_the_text() -> None:
    content = [
        {"type": "text", "content": "about 10"},
        {"type": "text", "content": "9", "styles": ["superscript"]},
        {"type": "text", "content": " neurons in CO", "styles": ["bold"]},
        {"type": "text", "content": "2", "styles": ["subscript"]},
    ]
    (block,) = _from_middle(_middle([_block("text", 0, "", content=content)])).pages[0].blocks

    assert _text(block).text == "about 10^{9} neurons in CO_{2}"


def test_image_bodies_are_skipped_and_captions_kept() -> None:
    image = {
        "type": "image",
        "index": 1,
        "bbox": [0.1, 0.3, 0.5, 0.6],
        "content": [
            {"type": "image_body", "index": 1, "bbox": [0.1, 0.3, 0.5, 0.6], "content": "ocr junk"},
            _block("image_caption", 1, "Fig 1. A mouse."),
        ],
    }
    page = _from_middle(_middle([_block("text", 0, "keep"), image])).pages[0]

    assert [(block.role, _text(block).text) for block in page.blocks] == [
        (BlockRole.BODY, "keep"),
        (BlockRole.CAPTION, "Fig 1. A mouse."),
    ]


def test_inline_spans_join_as_written_and_links_by_their_children() -> None:
    content = [
        {"type": "text", "content": "Mass was "},
        {"type": "equation_inline", "content": "12.5\\,g"},
        {"type": "text", "content": " (see "},
        {"type": "hyperlink", "url": "https://example.org", "content": _spans("Table 1")},
        {"type": "text", "content": ")."},
    ]
    equation = {"type": "equation", "index": 1, "bbox": [0.1, 0.3, 0.9, 0.4], "content": "E=mc^2"}
    page = _from_middle(_middle([_block("text", 0, "", content=content), equation])).pages[0]

    assert [_text(block).text for block in page.blocks] == [
        "Mass was 12.5\\,g (see Table 1).",
        "E=mc^2",
    ]


def test_list_items_are_list_entries_and_references_stay_references() -> None:
    items = {
        "type": "list",
        "index": 0,
        "bbox": [0.1, 0.1, 0.9, 0.5],
        "content": [_block("text", 0, "first"), _block("text", 1, "second")],
    }
    refs = {
        "type": "list",
        "index": 1,
        "bbox": [0.1, 0.6, 0.9, 0.9],
        "sub_type": "ref_text",
        "content": [_block("ref_text", 0, "1. Kang et al.")],
    }
    toc = {
        "type": "index",
        "index": 2,
        "bbox": [0.1, 0.9, 0.9, 0.95],
        "content": [_block("paragraph_title", 0, "Methods ... 3", level=2)],
    }
    page = _from_middle(_middle([items, refs, toc])).pages[0]

    assert [(block.role, _text(block).heading_level) for block in page.blocks] == [
        (BlockRole.LIST, None),
        (BlockRole.LIST, None),
        (BlockRole.REFERENCE, None),
        (BlockRole.LIST, None),
    ]


def _table_block(index: int, html: str) -> dict[str, Any]:
    bbox = [0.1, 0.25, 0.9, 0.5]
    return {
        "type": "table",
        "index": index,
        "bbox": bbox,
        "content": [
            _block("table_caption", index, "Table 1. Traits."),
            {"type": "table_body", "index": index, "bbox": bbox, "content": html},
        ],
    }


def test_table_body_html_maps_to_table_content() -> None:
    html = "<table><tr><td>alpha</td><td>beta</td></tr><tr><td colspan='2'>gamma</td></tr></table>"
    caption, table = _from_middle(_middle([_table_block(0, html)])).pages[0].blocks

    assert caption.role == BlockRole.CAPTION
    assert table.kind == BlockKind.TABLE
    assert table.bbox is not None and (table.bbox.x0, table.bbox.y1) == (0.1, 0.5)
    cells = {(cell.row, cell.column): cell for cell in _table(table).cells}
    assert [cells[(0, 0)].text, cells[(0, 1)].text, cells[(1, 0)].text] == [
        "alpha",
        "beta",
        "gamma",
    ]
    assert cells[(1, 0)].colspan == 2


def test_a_table_without_cells_is_not_kept_as_text() -> None:
    blocks = _from_middle(_middle([_table_block(0, "<p>scanned</p>")])).pages[0].blocks

    assert [block.role for block in blocks] == [BlockRole.CAPTION]


def test_continues_prev_links_across_pages_past_page_furniture() -> None:
    artifact = _from_middle(
        _middle(
            [_block("text", 0, "end of page one"), _block("footer", 1, "Journal | 1")],
            [
                _block("header", 0, "Journal"),
                _block("text", 1, "start of page two", continues_prev=True),
            ],
        )
    )
    first = artifact.pages[0].blocks[0]
    (second,) = artifact.pages[1].blocks

    assert first.continuation_of is None
    assert second.continuation_of == first.block_id


def test_a_continued_table_links_its_body_not_its_caption() -> None:
    html = "<table><tr><td>x</td></tr></table>"
    first = _table_block(0, html)
    figure = {
        "type": "image",
        "index": 1,
        "bbox": [0.1, 0.6, 0.9, 0.9],
        "content": [_block("image_caption", 1, "Fig 1. A mouse.")],
    }
    continued = _table_block(0, html) | {"continues_prev": True}
    continued["content"][0]["content"] = _spans("Table 1. (Continued)")
    first_page, second_page = _from_middle(_middle([first, figure], [continued])).pages
    caption, body = second_page.blocks

    assert caption.continuation_of is None
    assert body.continuation_of == first_page.blocks[1].block_id


def test_a_paragraph_continues_a_list_tail_and_reads_past_code() -> None:
    items = {
        "type": "list",
        "index": 1,
        "bbox": [0.1, 0.2, 0.9, 0.4],
        "content": [_block("text", 0, "first"), _block("text", 1, "second, about body mass of")],
    }
    code = {
        "type": "code",
        "index": 3,
        "bbox": [0.1, 0.5, 0.9, 0.6],
        "content": [
            {"type": "code_body", "index": 3, "bbox": [0.1, 0.5, 0.9, 0.6], "content": "print(1)"}
        ],
    }
    page_one, page_two = _from_middle(
        _middle(
            [_block("text", 0, "Intro."), items],
            [
                _block("text", 0, "adult mice, 25 g.", continues_prev=True),
                _block("text", 1, "We ran the following"),
                code,
                _block("text", 4, "script on every sample.", continues_prev=True),
            ],
        )
    ).pages

    assert page_two.blocks[0].continuation_of == page_one.blocks[2].block_id
    assert page_two.blocks[3].continuation_of == page_two.blocks[1].block_id


def test_continues_prev_skips_blocks_of_another_kind_and_role() -> None:
    blocks = (
        _from_middle(
            _middle(
                [
                    _block("text", 0, "body before the table"),
                    _table_block(1, "<table><tr><td>x</td></tr></table>"),
                    _block("text", 2, "body after the table", continues_prev=True),
                ]
            )
        )
        .pages[0]
        .blocks
    )

    assert blocks[-1].continuation_of == blocks[0].block_id


def test_bboxes_are_clamped_to_the_page() -> None:
    block = _block("text", 0, "edge", bbox=[-0.01, 0.0, 1.02, 0.1])
    (mapped,) = _from_middle(_middle([block])).pages[0].blocks

    assert mapped.bbox is not None
    assert (mapped.bbox.x0, mapped.bbox.x1) == (0.0, 1.0)


def test_another_mineru_version_is_refused() -> None:
    middle = _middle([_block("text", 0, "x")])
    middle["metadata"]["producer"]["version"] = "9.9.9"

    with pytest.raises(MiddleJsonContractError, match="'9.9.9'"):
        _from_middle(middle)


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.update(schema="docvortex.model"),
        lambda m: m.update(schema_version="1.0"),
        lambda m: m.update(pdf_info=m.pop("pages")),
        lambda m: m["extensions"].clear(),
        lambda m: m["pages"][0]["blocks"][0].update(type=["list"]),
        lambda m: m["extensions"]["docvortex_layout"]["pages"][0].update(width_pt=float("inf")),
        lambda m: m["pages"].append(m["pages"][0]),
    ],
    ids=[
        "model-json",
        "old-protocol",
        "3.x-pdf-info",
        "no-layout",
        "type-not-a-string",
        "infinite-page",
        "repeated-page",
    ],
)
def test_other_documents_are_invalid(change: Any) -> None:
    middle = _middle([_block("text", 0, "x")])
    change(middle)

    with pytest.raises(MiddleJsonContractError, match="invalid"):
        _from_middle(middle)


def test_a_page_without_a_size_is_refused() -> None:
    middle = _middle([_block("text", 0, "x")], [_block("text", 0, "y")])
    del middle["extensions"]["docvortex_layout"]["pages"][1]

    with pytest.raises(ValueError, match="no size for page index 1"):
        _from_middle(middle)


def test_a_document_without_text_is_refused() -> None:
    with pytest.raises(ValueError, match="no mappable") as caught:
        _from_middle(_middle([_block("page_number", 0, "1")]))
    assert not isinstance(caught.value, MiddleJsonContractError)


@pytest.mark.parametrize("raw", ["1001", "100000", "999999999"])
def test_oversized_single_axis_spans_are_capped(raw: str) -> None:
    cells = _cells_from_html(f'<table><tr><td rowspan="{raw}">x</td></tr></table>')
    assert cells[0].rowspan == HTML_CELL_MAX_SPAN
    assert cells[0].colspan == 1

    cells = _cells_from_html(f'<table><tr><td colspan="{raw}">x</td></tr></table>')
    assert cells[0].rowspan == 1
    assert cells[0].colspan == HTML_CELL_MAX_SPAN


@pytest.mark.parametrize("raw", ["1001", "100000", "999999999"])
def test_oversized_span_product_is_capped(raw: str) -> None:
    cells = _cells_from_html(f'<table><tr><td rowspan="{raw}" colspan="{raw}">x</td></tr></table>')
    assert cells[0].rowspan * cells[0].colspan == HTML_CELL_MAX_OCCUPIED
    assert cells[0].rowspan == 1
    assert cells[0].colspan == HTML_CELL_MAX_SPAN


def test_glyph_edges_highlight_inside_a_superscript() -> None:
    middle = _middle([_block("text", 0, "April 9"), _block("text", 1, "x")])
    # The stored text wraps the superscript the way text.py does. The edges are the glyphs.
    middle["pages"][0]["blocks"][0]["content"] = [
        {"type": "text", "content": "April 9"},
        {"type": "text", "content": "th", "styles": ["superscript"]},
    ]
    middle["extensions"]["bioparser_lines"] = {
        "version": 1,
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {
                        "index": 0,
                        "lines": [
                            {
                                "y0": 0.30,
                                "y1": 0.32,
                                "glyphs": [
                                    {"t": "April 9", "x0": 0.10, "x1": 0.40},
                                    {"t": "th", "x0": 0.40, "x1": 0.46},
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    content = _text(_from_middle(middle).pages[0].blocks[0])
    assert content.text == "April 9^{th}"
    boxes = boxes_for_range(content.lines, content.text.index("th"), content.text.index("th") + 2)
    assert len(boxes) == 1
    assert boxes[0].x0 == pytest.approx(0.40)
    assert boxes[0].x1 == pytest.approx(0.46)
    assert boxes[0].y0 == pytest.approx(0.30)


def test_an_unmeasured_span_does_not_take_the_next_glyphs() -> None:
    # A glyph longer than one character shares its own box. The measured letters keep theirs.
    middle = _middle([_block("text", 0, "catapult catalog")])
    middle["extensions"]["bioparser_lines"] = {
        "version": 1,
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {
                        "index": 0,
                        "lines": [
                            {
                                "y0": 0.20,
                                "y1": 0.22,
                                "glyphs": [
                                    {"t": "catapult", "x0": 0.10, "x1": 0.45},
                                    {"t": " ", "x0": 0.45, "x1": 0.48},
                                    {"t": "c", "x0": 0.50, "x1": 0.54},
                                    {"t": "a", "x0": 0.54, "x1": 0.58},
                                    {"t": "t", "x0": 0.58, "x1": 0.62},
                                    {"t": "a", "x0": 0.62, "x1": 0.66},
                                    {"t": "l", "x0": 0.66, "x1": 0.70},
                                    {"t": "o", "x0": 0.70, "x1": 0.74},
                                    {"t": "g", "x0": 0.74, "x1": 0.78},
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    content = _text(_from_middle(middle).pages[0].blocks[0])
    assert content.text == "catapult catalog"
    gap = boxes_for_range(content.lines, 0, len("catapult"))
    assert len(gap) == 1
    assert gap[0].x0 == pytest.approx(0.10)
    assert gap[0].x1 == pytest.approx(0.45)
    word = content.text.index("catalog")
    measured = boxes_for_range(content.lines, word, word + len("catalog"))
    assert len(measured) == 1
    assert measured[0].x0 == pytest.approx(0.50)
    assert measured[0].x1 == pytest.approx(0.78)


def test_a_removed_line_end_hyphen_does_not_take_a_later_hyphen() -> None:
    middle = _middle([_block("text", 0, "computing X-ray")])
    middle["extensions"]["bioparser_lines"] = {
        "version": 1,
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {
                        "index": 0,
                        "lines": [
                            {
                                "y0": 0.10,
                                "y1": 0.12,
                                "glyphs": [
                                    {"t": "c", "x0": 0.10, "x1": 0.12},
                                    {"t": "o", "x0": 0.12, "x1": 0.14},
                                    {"t": "m", "x0": 0.14, "x1": 0.16},
                                    {"t": "p", "x0": 0.16, "x1": 0.18},
                                    {"t": "u", "x0": 0.18, "x1": 0.20},
                                    {"t": "t", "x0": 0.20, "x1": 0.22},
                                    {"t": "-", "x0": 0.22, "x1": 0.24},
                                ],
                            },
                            {
                                "y0": 0.14,
                                "y1": 0.16,
                                "glyphs": [
                                    {"t": "i", "x0": 0.10, "x1": 0.12},
                                    {"t": "n", "x0": 0.12, "x1": 0.14},
                                    {"t": "g", "x0": 0.14, "x1": 0.16},
                                    {"t": " ", "x0": 0.16, "x1": 0.18},
                                    {"t": "X", "x0": 0.20, "x1": 0.24},
                                    {"t": "-", "x0": 0.24, "x1": 0.26},
                                    {"t": "r", "x0": 0.26, "x1": 0.28},
                                    {"t": "a", "x0": 0.28, "x1": 0.30},
                                    {"t": "y", "x0": 0.30, "x1": 0.32},
                                ],
                            },
                        ],
                    }
                ],
            }
        ],
    }
    content = _text(_from_middle(middle).pages[0].blocks[0])
    assert content.text == "computing X-ray"
    joined = boxes_for_range(content.lines, 0, len("computing"))
    assert [(box.x0, box.x1, box.y0) for box in joined] == [
        (0.10, 0.22, 0.10),
        (0.10, 0.16, 0.14),
    ]
    hyphen = content.text.index("-")
    kept = boxes_for_range(content.lines, hyphen, hyphen + 1)
    assert len(kept) == 1
    assert kept[0].x0 == pytest.approx(0.24)
    assert kept[0].y0 == pytest.approx(0.14)


def test_recognizer_columns_place_each_character() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mineru_glyph_edges",
        Path(__file__).resolve().parents[2] / "docker" / "patches" / "mineru_glyph_edges.py",
    )
    assert spec is not None and spec.loader is not None
    patch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patch)
    chars = patch._bioparser_ocr_chars("AB", [10.0, [1.0, 5.0]], [0.0, 10.0, 100.0, 30.0])
    assert chars is not None
    assert [item["char"] for item in chars] == ["A", "B"]
    assert chars[0]["bbox"] == pytest.approx([0.0, 10.0, 30.0, 30.0])
    assert chars[1]["bbox"] == pytest.approx([30.0, 10.0, 100.0, 30.0])
    assert patch._bioparser_ocr_chars("A", [4.0, [1.0]], [0.0, 0.0, 10.0, 40.0]) is None
    assert patch._bioparser_ocr_chars("AB", [10.0, [1.0]], [0.0, 0.0, 10.0, 4.0]) is None


def test_balanced_span_product_is_capped() -> None:
    cells = _cells_from_html('<table><tr><td rowspan="50" colspan="50">x</td></tr></table>')
    assert cells[0].rowspan * cells[0].colspan == HTML_CELL_MAX_OCCUPIED
    assert cells[0].rowspan == 20
    assert cells[0].colspan == 50
