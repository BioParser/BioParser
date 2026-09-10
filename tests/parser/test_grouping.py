# tests for word-to-block grouping
import time

import pytest

from bioparser.parser.backend.default.grouping import (
    TextBlock,
    WordBox,
    group_words_into_text_blocks,
)

LINE_TOLERANCE = 3.0
PARAGRAPH_GAP_FACTOR = 0.6


def words_for_line(tag: str, x0: float, top: float, count: int = 4) -> list[WordBox]:
    return [
        WordBox(
            text=f"{tag}{i}",
            x0=x0 + i * 30.0,
            x1=x0 + i * 30.0 + 25.0,
            top=top,
            bottom=top + 8.0,
        )
        for i in range(count)
    ]


def group(words: list[WordBox]) -> list[TextBlock]:
    return group_words_into_text_blocks(
        words,
        line_tolerance=LINE_TOLERANCE,
        paragraph_gap_factor=PARAGRAPH_GAP_FACTOR,
    )


def test_block_order_follows_page_position_not_flush_order() -> None:
    words: list[WordBox] = []
    for top in range(100, 400, 12):
        words += words_for_line("L", 50.0, float(top))
    for top in range(100, 160, 12):
        words += words_for_line("Ra", 350.0, float(top))
    for top in range(300, 400, 12):
        words += words_for_line("Rb", 350.0, float(top))

    blocks = group(words)

    assert [block.text[:2] for block in blocks] == ["L0", "Ra", "Rb"]


def test_line_geometry_matches_words() -> None:
    words = words_for_line("w", 50.0, 100.0)

    block = group(words)[0]

    assert block.x0 == min(word.x0 for word in words)
    assert block.x1 == max(word.x1 for word in words)
    assert block.top == min(word.top for word in words)
    assert block.bottom == max(word.bottom for word in words)


def test_single_word_page_produces_one_block() -> None:
    blocks = group([WordBox(text="solo", x0=10.0, x1=40.0, top=10.0, bottom=18.0)])

    assert len(blocks) == 1
    assert blocks[0].text == "solo"


@pytest.mark.parametrize("count", [500, 2000])
def test_non_overlapping_lines_stay_within_time_budget(count: int) -> None:
    words = [
        WordBox(
            text=f"w{i}",
            x0=(i % 40) * 20.0,
            x1=(i % 40) * 20.0 + 5.0,
            top=i * 10.0,
            bottom=i * 10.0 + 8.0,
        )
        for i in range(count)
    ]

    started = time.perf_counter()
    group(words)
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0, f"{count} non-overlapping lines took {elapsed:.1f}s"
