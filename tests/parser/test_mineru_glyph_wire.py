"""Reading the glyph geometry the MinerU image attaches to a middle.json."""

import pytest

from bioparser.parser.backend.mineru.glyph_wire import lines_by_block, parse_block


def test_parse_block_skips_what_it_cannot_use() -> None:
    lines = [
        {"y0": 0.1, "y1": 0.2, "glyphs": [{"t": "a", "x0": 0.1, "x1": 0.2}, {"t": "b"}, 7]},
        {"y0": 0.5, "y1": 0.4, "glyphs": [{"t": "c", "x0": 0.1, "x1": 0.2}]},
        {"y0": 0.3, "y1": 0.4, "glyphs": None},
        "not a line",
    ]
    geometry = parse_block(lines)
    assert [glyph.char for glyph in geometry.glyphs] == ["a"]
    assert geometry.formulas == []


def test_a_long_glyph_shares_its_box_in_equal_slices() -> None:
    geometry = parse_block([{"y0": 0, "y1": 1, "glyphs": [{"t": "ab", "x0": 0.2, "x1": 0.4}]}])
    assert [g.char for g in geometry.glyphs] == ["a", "b"]
    assert [(g.x0, g.x1) for g in geometry.glyphs] == [
        pytest.approx((0.2, 0.3)),
        pytest.approx((0.3, 0.4)),
    ]


def test_a_formula_falls_back_to_its_line_span_and_counts_the_letters_before_it() -> None:
    glyphs = [{"t": "a", "x0": 0.1, "x1": 0.2}, {"t": "", "x0": 0.3, "x1": 0.5, "latex": "x"}]
    geometry = parse_block([{"y0": 0.1, "y1": 0.2, "glyphs": glyphs}])
    (formula,) = geometry.formulas
    assert formula.anchor == 1
    assert formula.latex == "x"
    assert (formula.box.y0, formula.box.y1) == (0.1, 0.2)


def test_lines_by_block_needs_version_one_and_integer_keys() -> None:
    block = {"index": 2, "lines": []}
    extension = {
        "version": 1,
        "pages": [
            {"page_idx": 1, "blocks": [block, {"index": True, "lines": []}, 5]},
            {"page_idx": False, "blocks": [block]},
        ],
    }
    assert lines_by_block(extension) == {(1, 2): []}
    assert lines_by_block({**extension, "version": 2}) == {}
    assert lines_by_block(None) == {}
