"""The runtime half of the glyph patch, which the MinerU image installs next to MinerU."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "bioparser_glyphs",
    Path(__file__).resolve().parents[3]
    / "docker"
    / "patches"
    / "mineru"
    / "glyph_edges"
    / "bioparser_glyphs.py",
)
assert _SPEC is not None and _SPEC.loader is not None
glyphs: Any = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(glyphs)


def _unit(box: Any) -> list[float]:
    return [value / 100 for value in box]


def test_char_box_rejects_unusable_characters() -> None:
    assert glyphs.char_box({"char": "a", "bbox": [1, 2, 3, 4]}) == ("a", (1.0, 2.0, 3.0, 4.0))
    for bad in (
        None,
        {"char": "", "bbox": [1, 2, 3, 4]},
        {"char": "a", "bbox": None},
        {"char": "a", "bbox": [1, 2, 3]},
        {"char": "a", "bbox": ["x", 2, 3, 4]},
    ):
        assert glyphs.char_box(bad) is None


def test_keep_span_glyphs_copies_valid_characters_only() -> None:
    span = SimpleNamespace(
        metadata={"chars": [{"char": "a", "bbox": [0, 0, 1, 1]}, {"char": "", "bbox": []}, 7]}
    )
    glyphs.keep_span_glyphs(span)
    assert span.metadata["bioparser_glyphs"] == [{"char": "a", "bbox": [0.0, 0.0, 1.0, 1.0]}]
    empty = SimpleNamespace(metadata={"chars": []})
    glyphs.keep_span_glyphs(empty)
    assert "bioparser_glyphs" not in empty.metadata


def test_line_glyphs_uses_saved_characters_and_formula_boxes() -> None:
    text = SimpleNamespace(
        type="text",
        metadata={"bioparser_glyphs": [{"char": "a", "bbox": [10, 0, 20, 10]}]},
    )
    formula = SimpleNamespace(type="eq", metadata={}, bbox=(30, 1, 50, 9), content="\\( x^2 \\)")
    other = SimpleNamespace(type="image", metadata={}, bbox=(0, 0, 1, 1), content="")
    result = glyphs.line_glyphs([text, formula, other], _unit, "eq")
    assert result == [
        {"t": "a", "x0": 0.1, "x1": 0.2},
        {"t": "", "x0": 0.3, "x1": 0.5, "y0": 0.01, "y1": 0.09, "latex": "x^2"},
    ]


def test_output_lines_pairs_members_with_frozen_glyphs() -> None:
    items = [{"bbox": [0, 0, 1, 1]}, {"bbox": [0, 1, 1, 2]}]
    members = [SimpleNamespace(bioparser_glyphs=[{"t": "a"}]), SimpleNamespace()]
    assert glyphs.output_lines(items, members) == [
        {"bbox": [0, 0, 1, 1], "glyphs": [{"t": "a"}]},
        {"bbox": [0, 1, 1, 2], "glyphs": []},
    ]
    assert glyphs.output_lines([{"bbox": 1}], None) == [{"bbox": 1}]


def test_column_centers_use_the_middle_of_each_repeated_run() -> None:
    ids = [0, 5, 5, 5, 0, 7, 7]
    assert glyphs.column_centers(ids, [1, 5]) == [2.0, 5.5]


def test_ocr_chars_split_the_box_halfway_between_centers() -> None:
    result = glyphs.ocr_chars("ab", [10, [2, 8]], [0, 0, 100, 10])
    assert result == [
        {"char": "a", "bbox": [0.0, 0.0, 50.0, 10.0]},
        {"char": "b", "bbox": [50.0, 0.0, 100.0, 10.0]},
    ]


def test_ocr_chars_grows_a_short_column_count_to_the_last_center() -> None:
    result = glyphs.ocr_chars("ab", [4, [1, 8]], [0, 0, 100, 10])
    assert result is not None
    assert result[-1]["bbox"][2] == 100.0


@pytest.mark.parametrize(
    ("text", "info", "bbox"),
    [
        ("", [10, []], [0, 0, 100, 10]),
        ("ab", [10, [1]], [0, 0, 100, 10]),
        ("ab", [10, [1, 2]], [0, 0, 10, 100]),
        ("ab", [10, [1, 2]], [0, 0, 100]),
        ("ab", None, [0, 0, 100, 10]),
    ],
)
def test_ocr_chars_leaves_unplaceable_text_alone(text: str, info: Any, bbox: Any) -> None:
    assert glyphs.ocr_chars(text, info, bbox) is None


def test_sidecar_glyphs_drop_everything_on_one_bad_box() -> None:
    def page_box(box: Any) -> Any:
        return None if box is None else list(box)

    good = [{"char": "a", "bbox": [1, 2, 3, 4]}]
    assert glyphs.sidecar_glyphs(good, page_box) == [{"char": "a", "bbox": [1, 2, 3, 4]}]
    assert glyphs.sidecar_glyphs([*good, {"char": "b", "bbox": None}], page_box) is None
    assert glyphs.sidecar_glyphs([], page_box) is None


def _tagged(char: str, x0: float, y0: float, x1: float, y1: float, **extra: Any) -> dict[str, Any]:
    return {"char": char, "bbox": [x0, y0, x1, y1], **extra}


def test_tagged_lines_start_a_new_line_for_each_visual_row() -> None:
    chars = [
        _tagged("a", 0, 0, 10, 10),
        _tagged("b", 10, 1, 20, 11),
        _tagged("c", 0, 30, 10, 40),
        _tagged("h", 5, 0, 6, 10, text_is_visible=False),
        _tagged("\n", 0, 0, 1, 1),
    ]
    lines = glyphs.tagged_lines(chars, (100, 100))
    assert [[glyph["t"] for glyph in line["glyphs"]] for line in lines] == [["a", "b"], ["c"]]
    assert lines[0]["bbox"] == [0.0, 0.0, 0.2, 0.11]


def test_tagged_lines_ignore_a_bad_page_size() -> None:
    assert glyphs.tagged_lines([_tagged("a", 0, 0, 1, 1)], (0, 5)) == []
    assert glyphs.tagged_lines([_tagged("a", 0, 0, 1, 1)], None) == []


def _flat(index: int, glyph_text: str = "a") -> dict[str, Any]:
    return {"index": index, "lines": [{"bbox": [0, 0.1, 1, 0.2], "glyphs": [{"t": glyph_text}]}]}


def test_capture_follows_a_page_through_renumbering_and_naming() -> None:
    glyphs.start_capture()
    glyphs.note_flat([_flat(0, "a"), {"index": 1, "lines": []}, _flat(2, "c")])
    glyphs.remap_open_page([5, 6, 7])
    glyphs.set_page_idx(3)
    glyphs.note_flat([_flat(0, "z")])
    document = SimpleNamespace(extensions={"kept": True})
    result = glyphs.attach_lines(document)
    assert result.extensions["kept"] is True
    pages = result.extensions["bioparser_lines"]["pages"]
    assert [page["page_idx"] for page in pages] == [3, None]
    assert [block["index"] for block in pages[0]["blocks"]] == [5, 7]
    assert pages[0]["blocks"][0]["lines"] == [{"y0": 0.1, "y1": 0.2, "glyphs": [{"t": "a"}]}]


def test_attach_lines_leaves_a_document_without_glyphs_alone() -> None:
    glyphs.start_capture()
    document = SimpleNamespace(extensions={"kept": True})
    assert glyphs.attach_lines(document).extensions == {"kept": True}


def test_tagged_characters_keep_a_line_each() -> None:
    chars = [
        _tagged("A", 10.0, 10.0, 20.0, 22.0),
        _tagged("B", 24.0, 11.0, 34.0, 23.0),
        _tagged("C", 10.0, 40.0, 20.0, 52.0),
    ]
    lines = glyphs.tagged_lines(chars, (200.0, 100.0))
    assert len(lines) == 2
    assert [glyph["t"] for glyph in lines[0]["glyphs"]] == ["A", "B"]
    assert [glyph["t"] for glyph in lines[1]["glyphs"]] == ["C"]
    assert lines[0]["bbox"][1] == pytest.approx(0.10)
    assert lines[0]["bbox"][3] == pytest.approx(0.23)
    assert lines[1]["glyphs"][0]["x0"] == pytest.approx(0.05)
    assert lines[1]["bbox"][1] == pytest.approx(0.40)
    assert glyphs.tagged_lines([_tagged("A", 0, 0, 0, 1)], (10.0, 10.0)) == []


def test_recognizer_columns_place_each_character() -> None:
    chars = glyphs.ocr_chars("AB", [10.0, [1.0, 5.0]], [0.0, 10.0, 100.0, 30.0])
    assert chars is not None
    assert [item["char"] for item in chars] == ["A", "B"]
    assert chars[0]["bbox"] == pytest.approx([0.0, 10.0, 30.0, 30.0])
    assert chars[1]["bbox"] == pytest.approx([30.0, 10.0, 100.0, 30.0])
    assert glyphs.ocr_chars("A", [4.0, [1.0]], [0.0, 0.0, 10.0, 40.0]) is None
    assert glyphs.ocr_chars("AB", [10.0, [1.0]], [0.0, 0.0, 10.0, 4.0]) is None
    kept = glyphs.ocr_chars("ABCD", [10.0, [2.0, 6.0, 12.0, 16.0]], [0.0, 0.0, 160.0, 10.0])
    assert kept is not None
    assert [item["char"] for item in kept] == ["A", "B", "C", "D"]
    assert kept[-1]["bbox"][2] == pytest.approx(160.0)
    edges = [item["bbox"][0] for item in kept] + [kept[-1]["bbox"][2]]
    assert edges == sorted(set(edges))
