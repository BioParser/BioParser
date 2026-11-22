import { describe, expect, it } from "vitest";
import {
  buildQuotation,
  codePointOffsets,
  EvidenceMismatchError,
  indexTextBlocks,
  type Observation,
  type ParserArtifact,
} from "./quotation";

const BOX = { x0: 0.1, y0: 0.2, x1: 0.9, y1: 0.3 };

const PARSER: ParserArtifact = {
  schema_version: "1",
  checksum: "abc",
  parser: { name: "default", version: "1", configuration: {} },
  pages: [
    {
      page_number: 1,
      width: 612,
      height: 792,
      blocks: [
        {
          block_id: "b0",
          order: 0,
          kind: "text",
          role: "body",
          content: { type: "text", text: "Mus musculus weighed 12.5 kg" },
          bbox: BOX,
        },
        {
          block_id: "b1",
          order: 1,
          kind: "text",
          role: "body",
          content: { type: "text", text: "𝑀 = 12.5 kg" },
          bbox: null,
        },
        {
          block_id: "b2",
          order: 2,
          kind: "table",
          role: "body",
          content: { type: "table", cells: [] },
        },
      ],
    },
    {
      page_number: 2,
      width: 612,
      height: 792,
      blocks: [
        {
          block_id: "b3",
          order: 3,
          kind: "text",
          role: "body",
          content: { type: "text", text: "The mean was 1" },
          bbox: BOX,
        },
        {
          block_id: "b4",
          order: 4,
          kind: "text",
          role: "body",
          content: { type: "text", text: "4 g in adults" },
          bbox: BOX,
        },
      ],
    },
  ],
};

const BLOCKS = indexTextBlocks(PARSER);

function citation(text: string, ...evidence: [string, number, number][]) {
  return {
    kind: "citation" as const,
    text,
    evidence: evidence.map(([block_id, start, end]) => ({ block_id, start, end })),
  };
}

function observation(
  value: ReturnType<typeof citation>,
  unit: ReturnType<typeof citation>,
): Observation {
  return {
    traitCode: { kind: "interpretation", value: "body_mass", passage: value },
    verbatimTraitName: value,
    verbatimScientificName: value,
    verbatimTraitValue: value,
    verbatimTraitUnit: unit,
  };
}

describe("indexTextBlocks", () => {
  it("indexes text blocks with their page and keeps a missing bbox as null", () => {
    expect(BLOCKS.get("b0")).toEqual({
      blockId: "b0",
      pageNumber: 1,
      text: "Mus musculus weighed 12.5 kg",
      bbox: BOX,
      lines: [],
    });
    expect(BLOCKS.get("b1")?.bbox).toBeNull();
    expect(BLOCKS.get("b3")?.pageNumber).toBe(2);
  });

  it("leaves out table blocks", () => {
    expect(BLOCKS.has("b2")).toBe(false);
  });
});

describe("codePointOffsets", () => {
  it("counts a character outside the BMP as two UTF-16 units", () => {
    expect(codePointOffsets("a𝑀b")).toEqual([0, 1, 3, 4]);
  });
});

describe("buildQuotation", () => {
  it("marks the value and the unit in one block", () => {
    const quotation = buildQuotation(
      observation(citation("12.5", ["b0", 21, 25]), citation("kg", ["b0", 26, 28])),
      BLOCKS,
    );
    expect(quotation).toEqual([
      {
        blockId: "b0",
        pageNumber: 1,
        bbox: BOX,
        segments: [
          { text: "Mus musculus weighed ", mark: null },
          { text: "12.5", mark: "value" },
          { text: " ", mark: null },
          { text: "kg", mark: "unit" },
        ],
        highlights: [],
      },
    ]);
  });

  it("converts code point ranges for text outside the BMP", () => {
    // "𝑀" is one code point and two UTF-16 units, so a plain slice would be off by one.
    const [block] = buildQuotation(
      observation(citation("12.5", ["b1", 4, 8]), citation("kg", ["b1", 9, 11])),
      BLOCKS,
    );
    expect(block?.segments).toEqual([
      { text: "𝑀 = ", mark: null },
      { text: "12.5", mark: "value" },
      { text: " ", mark: null },
      { text: "kg", mark: "unit" },
    ]);
  });

  it("shows both blocks of a value that continues into a second block", () => {
    const quotation = buildQuotation(
      observation(citation("14", ["b3", 13, 14], ["b4", 0, 1]), citation("g", ["b4", 2, 3])),
      BLOCKS,
    );
    expect(quotation.map((block) => [block.blockId, block.pageNumber])).toEqual([
      ["b3", 2],
      ["b4", 2],
    ]);
    expect(quotation[0]?.segments).toEqual([
      { text: "The mean was ", mark: null },
      { text: "1", mark: "value" },
    ]);
    expect(quotation[1]?.segments).toEqual([
      { text: "4", mark: "value" },
      { text: " ", mark: null },
      { text: "g", mark: "unit" },
      { text: " in adults", mark: null },
    ]);
  });

  it("does not show the unit's block when the unit is in another block", () => {
    const quotation = buildQuotation(
      observation(citation("12.5", ["b0", 21, 25]), citation("g", ["b4", 2, 3])),
      BLOCKS,
    );
    expect(quotation.map((block) => block.blockId)).toEqual(["b0"]);
    expect(quotation[0]?.segments.some((segment) => segment.mark === "unit")).toBe(false);
  });

  it("keeps the value marked where the unit overlaps it", () => {
    const [block] = buildQuotation(
      observation(citation("12.5", ["b0", 21, 25]), citation("5 kg", ["b0", 24, 28])),
      BLOCKS,
    );
    expect(block?.segments.slice(1)).toEqual([
      { text: "12.5", mark: "value" },
      { text: " kg", mark: "unit" },
    ]);
  });

  it("keeps a block with no bbox", () => {
    const [block] = buildQuotation(
      observation(citation("12.5", ["b1", 4, 8]), citation("kg", ["b1", 9, 11])),
      BLOCKS,
    );
    expect(block?.bbox).toBeNull();
  });

  it("refuses evidence in a block the parser artifact does not have", () => {
    expect(() =>
      buildQuotation(
        observation(citation("12.5", ["b9", 0, 4]), citation("kg", ["b9", 5, 7])),
        BLOCKS,
      ),
    ).toThrow(EvidenceMismatchError);
  });

  it("refuses a range past the end of the block", () => {
    expect(() =>
      buildQuotation(
        observation(citation("12.5", ["b0", 26, 40]), citation("kg", ["b0", 26, 28])),
        BLOCKS,
      ),
    ).toThrow(EvidenceMismatchError);
  });
});

// Glyph edges for one line of text: each character is 0.02 wide from x = 0.1, and a space
// has no ink. Indexes are code points, as in the parser artifact.
function left(index: number): number {
  return 0.1 + index * 0.02;
}

function right(index: number): number {
  return left(index) + 0.02;
}

function line(text: string, start: number, end: number, y0: number) {
  const chars = [...text].slice(start, end);
  return {
    start,
    end,
    y0,
    y1: y0 + 0.05,
    x0: chars.map((char, i) => (char === " " ? null : left(start + i))),
    x1: chars.map((char, i) => (char === " " ? null : right(start + i))),
  };
}

function textBlock(blockId: string, text: string, lines: ReturnType<typeof line>[]) {
  return {
    block_id: blockId,
    order: 0,
    kind: "text" as const,
    role: "body" as const,
    content: { type: "text" as const, text, lines },
    bbox: BOX,
  };
}

const ONE_LINE = "Mus musculus weighed 12.5 kg";
const TWO_LINES = "means were 12.5 to 14.0 g";
const NON_BMP = "\u{1d440} = 12.5 kg";

const GLYPHS = indexTextBlocks({
  schema_version: "2",
  checksum: "abc",
  parser: { name: "default", version: "2", configuration: {} },
  pages: [
    {
      page_number: 1,
      width: 612,
      height: 792,
      blocks: [
        textBlock("g0", ONE_LINE, [line(ONE_LINE, 0, 28, 0.2)]),
        // The line breaks after "to ": the value 12.5 to 14.0 is on both lines.
        textBlock("g1", TWO_LINES, [line(TWO_LINES, 0, 19, 0.2), line(TWO_LINES, 19, 25, 0.3)]),
        textBlock("g2", NON_BMP, [line(NON_BMP, 0, 11, 0.5)]),
      ],
    },
  ],
});

describe("buildQuotation highlights", () => {
  it("boxes the value and the unit on one line", () => {
    const [block] = buildQuotation(
      observation(citation("12.5", ["g0", 21, 25]), citation("kg", ["g0", 26, 28])),
      GLYPHS,
    );
    expect(block?.highlights).toEqual([
      { mark: "value", bbox: { x0: left(21), y0: 0.2, x1: right(24), y1: 0.25 } },
      { mark: "unit", bbox: { x0: left(26), y0: 0.2, x1: right(27), y1: 0.25 } },
    ]);
  });

  it("boxes a value over a line break once on each line", () => {
    const [block] = buildQuotation(
      observation(citation("12.5 to 14.0", ["g1", 11, 23]), citation("g", ["g1", 24, 25])),
      GLYPHS,
    );
    expect(block?.highlights).toEqual([
      { mark: "value", bbox: { x0: left(11), y0: 0.2, x1: right(17), y1: 0.25 } },
      { mark: "value", bbox: { x0: left(19), y0: 0.3, x1: right(22), y1: 0.35 } },
      { mark: "unit", bbox: { x0: left(24), y0: 0.3, x1: right(24), y1: 0.35 } },
    ]);
  });

  it("keeps the value's box where the unit overlaps it", () => {
    const [block] = buildQuotation(
      observation(citation("12.5", ["g0", 21, 25]), citation("5 kg", ["g0", 24, 28])),
      GLYPHS,
    );
    expect(block?.highlights).toEqual([
      { mark: "value", bbox: { x0: left(21), y0: 0.2, x1: right(24), y1: 0.25 } },
      { mark: "unit", bbox: { x0: left(26), y0: 0.2, x1: right(27), y1: 0.25 } },
    ]);
  });

  it("reads the glyph edges by code point after a character outside the BMP", () => {
    const [block] = buildQuotation(
      observation(citation("12.5", ["g2", 4, 8]), citation("kg", ["g2", 9, 11])),
      GLYPHS,
    );
    expect(block?.segments.map((segment) => segment.text)).toEqual([
      "\u{1d440} = ",
      "12.5",
      " ",
      "kg",
    ]);
    expect(block?.highlights.map((highlight) => highlight.bbox.x0)).toEqual([left(4), left(9)]);
  });

  it("draws no box for a marked range with no ink", () => {
    const [block] = buildQuotation(
      observation(citation("12.5", ["g0", 21, 25]), citation(" ", ["g0", 25, 26])),
      GLYPHS,
    );
    expect(block?.highlights.map((highlight) => highlight.mark)).toEqual(["value"]);
  });

  it("draws no box in a block of schema 1, which has no lines", () => {
    const [block] = buildQuotation(
      observation(citation("12.5", ["b0", 21, 25]), citation("kg", ["b0", 26, 28])),
      BLOCKS,
    );
    expect(block?.highlights).toEqual([]);
    expect(block?.bbox).toEqual(BOX);
  });
});
