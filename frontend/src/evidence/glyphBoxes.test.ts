import { describe, expect, it } from "vitest";
import { boxesForRange, type TextLine } from "./glyphBoxes";

// "Hello" from tests/parser/test_models.py: one line, five inked characters.
const HELLO: TextLine = {
  start: 0,
  end: 5,
  y0: 0.2,
  y1: 0.3,
  x0: [0.1, 0.14, 0.18, 0.22, 0.26],
  x1: [0.14, 0.18, 0.22, 0.26, 0.3],
};

// "abc def" over two lines. The space ends the first line and has no ink.
const TWO_LINES: TextLine[] = [
  { start: 0, end: 4, y0: 0.1, y1: 0.15, x0: [0.1, 0.2, 0.3, null], x1: [0.2, 0.3, 0.4, null] },
  { start: 4, end: 7, y0: 0.2, y1: 0.25, x0: [0.1, 0.2, 0.3], x1: [0.2, 0.3, 0.4] },
];

// "10 km^{2}" as MinerU stores a superscript: the ^{ and } markers have no ink, and the 2
// is smaller and raised.
const SUPERSCRIPT: TextLine = {
  start: 0,
  end: 9,
  y0: 0.4,
  y1: 0.45,
  x0: [0.1, 0.12, null, 0.16, 0.18, null, null, 0.21, null],
  x1: [0.12, 0.14, null, 0.18, 0.205, null, null, 0.22, null],
};

describe("boxesForRange", () => {
  it("uses only the glyph edges inside a range in the middle of a word", () => {
    expect(boxesForRange([HELLO], 2, 4)).toEqual([{ x0: 0.18, y0: 0.2, x1: 0.26, y1: 0.3 }]);
  });

  it("gives one box per line for a range over a line break", () => {
    expect(boxesForRange(TWO_LINES, 1, 6)).toEqual([
      { x0: 0.2, y0: 0.1, x1: 0.4, y1: 0.15 },
      { x0: 0.1, y0: 0.2, x1: 0.3, y1: 0.25 },
    ]);
  });

  it("gives no box for a range of characters without ink", () => {
    expect(boxesForRange(TWO_LINES, 3, 4)).toEqual([]);
  });

  it("gives no box for a range outside every line", () => {
    expect(boxesForRange(TWO_LINES, 7, 9)).toEqual([]);
    expect(boxesForRange([HELLO], 5, 8)).toEqual([]);
  });

  it("gives no box for an empty range", () => {
    expect(boxesForRange([HELLO], 2, 2)).toEqual([]);
  });

  it("boxes a superscript unit from its measured letters, not MinerU's markers", () => {
    // km^{2}: from the k to the 2.
    expect(boxesForRange([SUPERSCRIPT], 3, 9)).toEqual([{ x0: 0.16, y0: 0.4, x1: 0.22, y1: 0.45 }]);
    // The 2 alone: the markers around it add nothing.
    expect(boxesForRange([SUPERSCRIPT], 5, 9)).toEqual([{ x0: 0.21, y0: 0.4, x1: 0.22, y1: 0.45 }]);
  });
});
