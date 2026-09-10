from pathlib import Path

import pytest

from bioparser.parser import (
    DefaultParser,
    ParserBackendUnavailableError,
    UnsupportedDocumentError,
    get_parser,
)
from bioparser.parser.backend.default.grouping import WordBox, group_words_into_text_blocks
from bioparser.parser.backend.default.parser import DEFAULT_PARSER_NAME
from bioparser.parser.checksum import sha256_file
from bioparser.parser.models import ParserArtifact

FIXTURE = Path(__file__).parent / "fixtures" / "plos-biology-3000248.pdf"

EMPTY_PAGE_PDF = b"""%PDF-1.1
1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj
2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj
3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>endobj
trailer<< /Root 1 0 R >>
%%EOF
"""


def test_fixture_pdf_to_artifact() -> None:
    artifact = DefaultParser().parse(FIXTURE)
    restored = ParserArtifact.model_validate_json(artifact.model_dump_json())
    assert restored == artifact
    assert artifact.parser.name == DEFAULT_PARSER_NAME
    assert artifact.checksum == sha256_file(FIXTURE)
    assert [page.page_number for page in artifact.pages] == [1, 2, 3]
    page_one_text = " ".join(
        block.content.text for block in artifact.pages[0].blocks if block.content.type == "text"
    )
    assert "Broadening the scope of PLOS Biology" in page_one_text
    assert "PLOS Biology is committed" in page_one_text
    again = DefaultParser().parse(FIXTURE)
    assert again.model_dump(mode="json") == artifact.model_dump(mode="json")


def test_parse_rejects_pdf_without_digital_text(tmp_path: Path) -> None:
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(EMPTY_PAGE_PDF)
    with pytest.raises(UnsupportedDocumentError, match="Failed to parse"):
        DefaultParser().parse(empty)


def test_parse_rejects_malformed_pdf(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"this is not a pdf")
    with pytest.raises(UnsupportedDocumentError, match="malformed"):
        DefaultParser().parse(broken)


def test_groups_lines_and_paragraphs() -> None:
    words = [
        WordBox("Hello", 10, 40, 20, 30),
        WordBox("world", 45, 80, 21, 31),
        WordBox("again", 10, 50, 34, 44),
        WordBox("Next", 10, 40, 80, 90),
        WordBox("paragraph", 45, 100, 81, 91),
    ]
    blocks = group_words_into_text_blocks(words, line_tolerance=3.0, paragraph_gap_factor=0.6)
    assert [block.text for block in blocks] == ["Hello world again", "Next paragraph"]


def test_joins_hyphenated_line_wrap() -> None:
    words = [
        WordBox("signifi-", 10, 60, 20, 30),
        WordBox("cant", 10, 40, 34, 44),
    ]
    blocks = group_words_into_text_blocks(words, line_tolerance=3.0, paragraph_gap_factor=0.6)
    assert len(blocks) == 1
    assert blocks[0].text == "significant"
    assert [blocks[0].text[span.start : span.end] for span in blocks[0].spans] == [
        "signifi",
        "cant",
    ]
    assert [span.line for span in blocks[0].spans] == [0, 1]


def test_wrapped_large_lines_stay_one_block() -> None:
    words = [
        WordBox("Broadening", 10, 200, 20, 40),
        WordBox("Reports", 10, 180, 44, 64),
        WordBox("Body", 10, 80, 100, 110),
    ]
    blocks = group_words_into_text_blocks(words, line_tolerance=3.0, paragraph_gap_factor=0.6)
    assert [block.text for block in blocks] == ["Broadening Reports", "Body"]


def test_small_heading_does_not_merge_into_taller_title() -> None:
    words = [
        WordBox("EDITORIAL", 10, 80, 20, 28),
        WordBox("Broadening", 10, 200, 36, 56),
    ]
    blocks = group_words_into_text_blocks(words, line_tolerance=3.0, paragraph_gap_factor=0.6)
    assert [block.text for block in blocks] == ["EDITORIAL", "Broadening"]


def test_same_baseline_columns_are_separate_lines() -> None:
    words = [
        WordBox("left", 20, 80, 50, 60),
        WordBox("right", 200, 260, 50, 60),
        WordBox("more", 265, 310, 51, 61),
    ]
    blocks = group_words_into_text_blocks(words, line_tolerance=3.0, paragraph_gap_factor=0.6)
    assert [block.text for block in blocks] == ["left", "right more"]


def test_vertically_close_columns_do_not_merge() -> None:
    words = [
        WordBox("left", 10, 80, 20, 30),
        WordBox("right", 200, 280, 32, 42),
    ]
    blocks = group_words_into_text_blocks(words, line_tolerance=3.0, paragraph_gap_factor=0.6)
    assert [block.text for block in blocks] == ["left", "right"]


def test_interleaved_columns_merge_independently() -> None:
    words = [
        WordBox("L1", 10, 80, 20, 30),
        WordBox("R1", 200, 400, 22, 32),
        WordBox("L2", 10, 80, 34, 44),
        WordBox("R2", 200, 400, 36, 46),
        WordBox("R3", 200, 380, 50, 60),
    ]
    blocks = group_words_into_text_blocks(words, line_tolerance=3.0, paragraph_gap_factor=0.6)
    assert [block.text for block in blocks] == ["L1 L2", "R1 R2 R3"]


def _column_paragraph(
    tokens: list[str],
    *,
    x0: float,
    x1: float,
    top: float,
    line_height: float = 10.0,
    line_gap: float = 4.0,
) -> tuple[list[WordBox], float]:
    """Build one column paragraph; return boxes and the last line's bottom."""
    boxes: list[WordBox] = []
    y = top
    bottom = top
    for token in tokens:
        bottom = y + line_height
        boxes.append(WordBox(token, x0, x1, y, bottom))
        y = bottom + line_gap
    return boxes, bottom


def test_two_column_multi_paragraph_reading_order() -> None:
    """Several paragraphs per column, plus a mid-page banner.

    Visual (top, x0) order would interleave left/right paragraphs. Reading
    order follows the open-column tracks from paragraph formation, so a
    tiny left-edge jitter (as in real PDFs) must not reshuffle the column.
    A spanning banner is a barrier.
    """
    left_x0, left_x1 = 10.0, 90.0
    right_x0, right_x1 = 220.0, 400.0
    para_gap = 20.0

    words: list[WordBox] = []
    left1, left1_bottom = _column_paragraph(
        ["Mice", "were", "housed"],
        x0=left_x0,
        x1=left_x1,
        top=20.0,
    )
    right1, right1_bottom = _column_paragraph(
        ["Samples", "were", "collected", "daily"],
        x0=right_x0,
        x1=right_x1,
        top=22.0,
    )
    left2, left2_bottom = _column_paragraph(
        ["Body", "mass", "increased"],
        x0=left_x0 + 0.0004,
        x1=left_x1,
        top=left1_bottom + para_gap,
    )
    right2, right2_bottom = _column_paragraph(
        ["Controls", "remained", "stable"],
        x0=right_x0 + 0.0003,
        x1=right_x1,
        top=right1_bottom + para_gap,
    )
    left3, left3_bottom = _column_paragraph(
        ["Survival", "did", "not", "differ"],
        x0=left_x0 - 0.0002,
        x1=left_x1,
        top=left2_bottom + para_gap,
    )
    right3, right3_bottom = _column_paragraph(
        ["Variance", "was", "low"],
        x0=right_x0 - 0.0001,
        x1=right_x1,
        top=right2_bottom + para_gap,
    )
    banner_top = max(left3_bottom, right3_bottom) + para_gap
    banner = WordBox("Abstract", 10.0, 400.0, banner_top, banner_top + 12.0)
    below_top = banner.bottom + para_gap
    left4, _ = _column_paragraph(
        ["Methods", "follow"],
        x0=left_x0,
        x1=left_x1,
        top=below_top,
    )
    right4, _ = _column_paragraph(
        ["Results", "are", "shown"],
        x0=right_x0,
        x1=right_x1,
        top=below_top + 2.0,
    )
    words.extend(left1)
    words.extend(right1)
    words.extend(left2)
    words.extend(right2)
    words.extend(left3)
    words.extend(right3)
    words.append(banner)
    words.extend(left4)
    words.extend(right4)

    blocks = group_words_into_text_blocks(words, line_tolerance=3.0, paragraph_gap_factor=0.6)
    assert [block.text for block in blocks] == [
        "Mice were housed",
        "Body mass increased",
        "Survival did not differ",
        "Samples were collected daily",
        "Controls remained stable",
        "Variance was low",
        "Abstract",
        "Methods follow",
        "Results are shown",
    ]


def test_narrow_x_overlap_is_a_separate_column() -> None:
    words = [
        WordBox("left", 10, 104, 20, 30),
        WordBox("right", 100, 200, 26, 36),
        WordBox("left2", 10, 104, 34, 44),
    ]
    blocks = group_words_into_text_blocks(words, line_tolerance=3.0, paragraph_gap_factor=0.6)
    assert [block.text for block in blocks] == ["left left2", "right"]


def test_line_overlapping_two_columns_closes_them() -> None:
    words = [
        WordBox("L", 10, 80, 20, 30),
        WordBox("R", 200, 280, 20, 30),
        WordBox("wide", 10, 280, 50, 60),
    ]
    blocks = group_words_into_text_blocks(words, line_tolerance=3.0, paragraph_gap_factor=0.6)
    assert [block.text for block in blocks] == ["L", "R", "wide"]


def test_x_gap_wider_than_word_height_starts_new_column() -> None:
    words = [
        WordBox("leftcol", 10, 100, 20, 30),
        WordBox("rightcol", 116, 200, 20, 30),
    ]
    blocks = group_words_into_text_blocks(words, line_tolerance=3.0, paragraph_gap_factor=0.6)
    assert [block.text for block in blocks] == ["leftcol", "rightcol"]


def test_x_gap_narrower_than_word_height_stays_one_line() -> None:
    words = [
        WordBox("Broadening", 10, 180, 20, 38),
        WordBox("the", 186, 220, 20, 38),
    ]
    blocks = group_words_into_text_blocks(words, line_tolerance=3.0, paragraph_gap_factor=0.6)
    assert [block.text for block in blocks] == ["Broadening the"]


def test_wrapped_substring_covers_two_lines() -> None:
    words = [
        WordBox("Hello", 10, 40, 20, 30),
        WordBox("world", 45, 80, 21, 31),
        WordBox("again", 10, 50, 34, 44),
    ]
    blocks = group_words_into_text_blocks(words, line_tolerance=3.0, paragraph_gap_factor=0.6)
    text = blocks[0].text
    start = text.index("world again")
    end = start + len("world again")
    hit_lines = {span.line for span in blocks[0].spans if span.start < end and span.end > start}
    assert hit_lines == {0, 1}


def test_unknown_parser_backend_is_unavailable() -> None:
    with pytest.raises(ParserBackendUnavailableError, match="Unknown parser backend"):
        get_parser("nope")
