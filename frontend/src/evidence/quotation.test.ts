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
    trait: { kind: "interpretation", value: "body_mass", passage: value },
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