import type { components } from "../api/schema";
import { boxesForRange, type TextLine } from "./glyphBoxes";

type Schemas = components["schemas"];
export type ParserArtifact = Schemas["ParserArtifact"];
export type Observation = Schemas["Observation"];
export type BoundingBox = Schemas["BoundingBox"];
type TextRange = Schemas["TextRange"];

// A text block from the parser artifact. Citations only point at text blocks.
export interface TextBlock {
  blockId: string;
  pageNumber: number;
  text: string;
  bbox: BoundingBox | null;
  // The block's text lines with a glyph edge per character. Empty when the parser stored
  // none: an artifact of schema 1, a MinerU block before #364, an OCR run.
  lines: TextLine[];
}

export type Mark = "value" | "unit";

// A run of block text, marked when it is the value or the unit.
export interface Segment {
  text: string;
  mark: Mark | null;
}

// A box around marked text on the PDF page, one per line the marked run touches.
export interface Highlight {
  mark: Mark;
  bbox: BoundingBox;
}

export interface QuotedBlock {
  blockId: string;
  pageNumber: number;
  bbox: BoundingBox | null;
  segments: Segment[];
  // Empty when the block has no lines, or no marked character has ink.
  highlights: Highlight[];
}

// The extraction artifact points at a block or a range the parser artifact does not have.
export class EvidenceMismatchError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "EvidenceMismatchError";
  }
}

export function indexTextBlocks(artifact: ParserArtifact): Map<string, TextBlock> {
  const blocks = new Map<string, TextBlock>();
  for (const page of artifact.pages) {
    for (const block of page.blocks) {
      if (block.content.type !== "text") {
        continue;
      }
      blocks.set(block.block_id, {
        blockId: block.block_id,
        pageNumber: page.page_number,
        text: block.content.text,
        bbox: block.bbox ?? null,
        lines: block.content.lines ?? [],
      });
    }
  }
  return blocks;
}

// The UTF-16 offset of each code point index in text, plus one for the end of the text.
// Ranges count code points (Python str indexes), and JavaScript strings count UTF-16 units.
export function codePointOffsets(text: string): number[] {
  const offsets = [0];
  let offset = 0;
  for (const char of text) {
    offset += char.length;
    offsets.push(offset);
  }
  return offsets;
}

// The value's blocks in evidence order, each split into segments and with a box around each
// marked run. The unit is marked only in those blocks. Where the two overlap, the value wins.
export function buildQuotation(
  observation: Observation,
  blocks: ReadonlyMap<string, TextBlock>,
): QuotedBlock[] {
  const valueRanges = measurementRanges(observation);
  const unitRanges = observation.verbatimTraitUnit?.evidence ?? [];
  const blockIds = [...new Set(valueRanges.map((range) => range.block_id))];

  return blockIds.map((blockId) => {
    const block = blocks.get(blockId);
    if (block === undefined) {
      throw new EvidenceMismatchError(`The parser artifact has no text block ${blockId}.`);
    }
    // One mark per code point, the unit of the ranges and of the glyph edges.
    const offsets = codePointOffsets(block.text);
    const marks: (Mark | null)[] = new Array(offsets.length - 1).fill(null);
    markRanges(marks, block, unitRanges, "unit");
    markRanges(marks, block, valueRanges, "value");
    return {
      blockId,
      pageNumber: block.pageNumber,
      bbox: block.bbox,
      segments: toSegments(block.text, offsets, marks),
      highlights: toHighlights(block.lines, marks),
    };
  });
}

// The printed value, or the printed bounds when the paper gives no single value.
function measurementRanges(observation: Observation): TextRange[] {
  const value = observation.verbatimTraitValue;
  if (value != null) {
    return value.evidence;
  }
  return [observation.measurementValueMin, observation.measurementValueMax].flatMap((bound) =>
    bound == null ? [] : bound.kind === "citation" ? bound.evidence : bound.passage.evidence,
  );
}

function markRanges(
  marks: (Mark | null)[],
  block: TextBlock,
  ranges: TextRange[],
  mark: Mark,
): void {
  for (const range of ranges) {
    if (range.block_id !== block.blockId) {
      continue;
    }
    if (range.start < 0 || range.end > marks.length || range.start >= range.end) {
      throw new EvidenceMismatchError(
        `The range [${range.start}, ${range.end}) is outside block ${block.blockId}.`,
      );
    }
    marks.fill(mark, range.start, range.end);
  }
}

// Runs of equal marks, sliced from the text with the UTF-16 offset of each code point.
function toSegments(text: string, offsets: number[], marks: (Mark | null)[]): Segment[] {
  const segments: Segment[] = [];
  for (const [start, end, mark] of runs(marks)) {
    segments.push({ text: text.slice(offsets[start], offsets[end]), mark });
  }
  return segments;
}

// The glyph boxes of each marked run. A run that is not marked gets no box.
function toHighlights(lines: TextLine[], marks: (Mark | null)[]): Highlight[] {
  const highlights: Highlight[] = [];
  for (const [start, end, mark] of runs(marks)) {
    if (mark === null) {
      continue;
    }
    for (const bbox of boxesForRange(lines, start, end)) {
      highlights.push({ mark, bbox });
    }
  }
  return highlights;
}

// [start, end, mark] for each run of equal marks, in order. An empty text has no run.
function runs(marks: (Mark | null)[]): [number, number, Mark | null][] {
  const found: [number, number, Mark | null][] = [];
  let start = 0;
  for (let i = 1; i <= marks.length; i++) {
    if (i === marks.length || marks[i] !== marks[start]) {
      found.push([start, i, marks[start] ?? null]);
      start = i;
    }
  }
  return found;
}
