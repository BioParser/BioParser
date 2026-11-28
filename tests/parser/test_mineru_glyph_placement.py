"""How measured glyph edges are placed onto a block's stored text.

Each test builds the glyphs MinerU measured, one dict per glyph, and checks the box every
stored character ends up with. A run is a ``TextLine``: a stretch of characters on one
drawn line. ``None`` is a stored character with no ink.
"""

from typing import Any

from bioparser.parser.backend.mineru.glyphs import text_lines_for_block

Glyph = dict[str, Any]
# (start, end, [(character, x0, x1), ...]) for one run.
Run = tuple[int, int, list[tuple[str, float | None, float | None]]]


def ink(text: str, x0: float, x1: float) -> Glyph:
    return {"t": text, "x0": x0, "x1": x1}


def formula(x0: float, x1: float, latex: str | None = None) -> Glyph:
    glyph: Glyph = {"t": "", "x0": x0, "x1": x1}
    if latex is not None:
        glyph["latex"] = latex
    return glyph


def placed(text: str, *drawn_lines: list[Glyph]) -> list[Run]:
    """The runs for ``text``, given one list of glyphs for each drawn line."""
    raw = [
        {"y0": 0.1 + 0.1 * index, "y1": 0.15 + 0.1 * index, "glyphs": glyphs}
        for index, glyphs in enumerate(drawn_lines)
    ]
    return [
        (
            line.start,
            line.end,
            [
                (text[line.start + offset], x0, x1)
                for offset, (x0, x1) in enumerate(zip(line.x0, line.x1, strict=True))
            ],
        )
        for line in text_lines_for_block(text, raw)
    ]


def test_a_formula_box_goes_to_its_latex() -> None:
    glyphs = [
        ink("a", 0.1, 0.15),
        ink(" ", 0.15, 0.17),
        formula(0.2, 0.4, "x^2"),
        ink(" ", 0.4, 0.42),
        ink("b", 0.5, 0.55),
    ]
    runs = placed("a x^2 b", glyphs)
    assert runs[1] == (2, 5, [("x", 0.2, 0.4), ("^", 0.2, 0.4), ("2", 0.2, 0.4)])
    assert runs[0][2] == [("a", 0.1, 0.15), (" ", 0.15, 0.17)]
    assert runs[2][2] == [(" ", 0.4, 0.42), ("b", 0.5, 0.55)]


def test_math_delimiters_around_the_latex_stay_empty() -> None:
    glyphs = [ink("a", 0.1, 0.15), formula(0.2, 0.4, "x^2"), ink("b", 0.5, 0.55)]
    runs = placed("a\\(x^2\\)b", glyphs)
    assert [run[:2] for run in runs] == [(0, 1), (3, 6), (8, 9)]
    assert runs[1][2] == [("x", 0.2, 0.4), ("^", 0.2, 0.4), ("2", 0.2, 0.4)]


def test_latex_that_starts_like_the_next_letter_is_not_taken_by_it() -> None:
    # The stored text reads ab_1. The formula owns b_1, not the drawn b after it.
    glyphs = [
        ink("a", 0.1, 0.15),
        formula(0.2, 0.4, "b_1"),
        ink(" ", 0.55, 0.57),
        ink("c", 0.6, 0.65),
    ]
    runs = placed("ab_1 c", glyphs)
    assert runs[1] == (1, 4, [("b", 0.2, 0.4), ("_", 0.2, 0.4), ("1", 0.2, 0.4)])
    assert runs[2][2] == [(" ", 0.55, 0.57), ("c", 0.6, 0.65)]


def test_a_drawn_letter_that_is_not_stored_is_dropped() -> None:
    glyphs = [
        ink("a", 0.1, 0.15),
        formula(0.2, 0.4, "b_1"),
        ink("b", 0.5, 0.55),
        ink("c", 0.6, 0.65),
    ]
    runs = placed("ab_1c", glyphs)
    assert runs == [
        (0, 1, [("a", 0.1, 0.15)]),
        (1, 4, [("b", 0.2, 0.4), ("_", 0.2, 0.4), ("1", 0.2, 0.4)]),
        (4, 5, [("c", 0.6, 0.65)]),
    ]


def test_spacing_in_the_stored_latex_does_not_matter() -> None:
    glyphs = [ink("a", 0.1, 0.15), formula(0.2, 0.4, "x+y"), ink("b", 0.5, 0.55)]
    runs = placed("a x + y b", glyphs)
    # The spaces inside the formula belong to it. The spaces around it have no ink.
    assert runs[1] == (
        2,
        7,
        [("x", 0.2, 0.4), (" ", 0.2, 0.4), ("+", 0.2, 0.4), (" ", 0.2, 0.4), ("y", 0.2, 0.4)],
    )
    assert [run[:2] for run in runs] == [(0, 1), (2, 7), (8, 9)]


def test_spaces_in_the_latex_itself_are_ignored_too() -> None:
    glyphs = [ink("a", 0.1, 0.15), formula(0.2, 0.4, "x + y"), ink("b", 0.5, 0.55)]
    runs = placed("a x+y b", glyphs)
    assert runs[1] == (2, 5, [("x", 0.2, 0.4), ("+", 0.2, 0.4), ("y", 0.2, 0.4)])


def test_a_formula_between_repeated_letters_keeps_its_place() -> None:
    glyphs = [ink("x", 0.1, 0.15), formula(0.2, 0.4, "x^2"), ink("x", 0.5, 0.55)]
    runs = placed("x x^2 x", glyphs)
    assert runs == [
        (0, 1, [("x", 0.1, 0.15)]),
        (2, 5, [("x", 0.2, 0.4), ("^", 0.2, 0.4), ("2", 0.2, 0.4)]),
        (6, 7, [("x", 0.5, 0.55)]),
    ]


def test_latex_the_text_rewrote_shares_the_formula_box_where_it_still_matches() -> None:
    # The stored text writes x^{2}. The braces are markers with no ink.
    glyphs = [ink("a", 0.1, 0.15), formula(0.2, 0.4, "x^2"), ink("b", 0.5, 0.55)]
    runs = placed("ax^{2}b", glyphs)
    assert runs[1] == (1, 5, [("x", 0.2, 0.4), ("^", 0.2, 0.4), ("{", None, None), ("2", 0.2, 0.4)])


def test_a_formula_with_no_latex_boxes_the_text_that_has_no_ink() -> None:
    glyphs = [ink("a", 0.1, 0.15), formula(0.2, 0.4), ink("b", 0.5, 0.55)]
    assert placed("aXb", glyphs) == [
        (0, 1, [("a", 0.1, 0.15)]),
        (1, 2, [("X", 0.2, 0.4)]),
        (2, 3, [("b", 0.5, 0.55)]),
    ]


def test_a_formula_with_no_latex_does_not_hide_one_that_has_latex() -> None:
    glyphs = [
        ink("a", 0.1, 0.15),
        formula(0.2, 0.3, "X"),
        formula(0.3, 0.4),
        ink("b", 0.5, 0.55),
    ]
    assert placed("aXYb", glyphs) == [
        (0, 1, [("a", 0.1, 0.15)]),
        (1, 2, [("X", 0.2, 0.3)]),
        (2, 3, [("Y", 0.3, 0.4)]),
        (3, 4, [("b", 0.5, 0.55)]),
    ]


def test_a_newline_in_the_text_is_like_a_space() -> None:
    # The drawn space has ink, so the newline in its place takes that box.
    glyphs = [ink("ab", 0.1, 0.2), ink(" ", 0.2, 0.25), ink("cd", 0.3, 0.4)]
    (run,) = placed("ab\ncd", glyphs)
    assert run[2][2] == ("\n", 0.2, 0.25)


def test_a_newline_with_no_drawn_space_stays_empty() -> None:
    (run,) = placed("ab\ncd", [ink("abcd", 0.1, 0.5)])
    assert run[2][2] == ("\n", None, None)
    assert [item[0] for item in run[2]] == ["a", "b", "\n", "c", "d"]


def test_a_word_broken_across_lines_is_one_run_on_each_line() -> None:
    first = [ink("ab", 0.1, 0.2)]
    second = [ink("cd", 0.1, 0.2)]
    assert placed("abcd", first, second) == [
        (0, 2, [("a", 0.1, 0.15), ("b", 0.15, 0.2)]),
        (2, 4, [("c", 0.1, 0.15), ("d", 0.15, 0.2)]),
    ]


def test_a_hyphen_removed_at_the_line_break_is_dropped() -> None:
    first = [ink("ab-", 0.1, 0.4)]
    second = [ink("cd", 0.1, 0.2)]
    runs = placed("abcd", first, second)
    assert [run[:2] for run in runs] == [(0, 2), (2, 4)]
