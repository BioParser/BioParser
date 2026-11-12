import type { components } from "../api/schema";

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
}

export type Mark = "value" | "unit"; 

// A run of block text, marked when it is the value or the unit.
export interface Segment {
  text: string;
  mark: Mark | null;
}

export interface QuotedBlock {
  blockId: string;
  pageNumber: number;
  bbox: BoundingBox | null;
  segments: Segment[];
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

// The value's blocks in evidence order, each split into segments. The unit is marked only
// in those blocks. Where the two overlap, the value wins.
export function buildQuotation(
  observation: Observation,
  blocks: ReadonlyMap<string, TextBlock>,
): QuotedBlock[] {
  const valueRanges = observation.verbatimTraitValue.evidence;
  const unitRanges = observation.verbatimTraitUnit.evidence;
  const blockIds = [...new Set(valueRanges.map((range) => range.block_id))];

  return blockIds.map((blockId) => {
    const block = blocks.get(blockId);
    if (block === undefined) {
      throw new EvidenceMismatchError(`The parser artifact has no text block ${blockId}.`);
    }
    const offsets = codePointOffsets(block.text);
    const marks: (Mark | null)[] = new Array(block.text.length).fill(null);
    markRanges(marks, offsets, block, unitRanges, "unit");
    markRanges(marks, offsets, block, valueRanges, "value");
    return {
      blockId,
      pageNumber: block.pageNumber,
      bbox: block.bbox,
      segments: toSegments(block.text, marks),
    };
  });
}

function markRanges(
  marks: (Mark | null)[],
  offsets: number[],
  block: TextBlock,
  ranges: TextRange[],
  mark: Mark,
): void {
  for (const range of ranges) {
    if (range.block_id !== block.blockId) {
      continue;
    }
    const start = offsets[range.start];
    const end = offsets[range.end];
    if (start === undefined || end === undefined || start >= end) {
      throw new EvidenceMismatchError(
        `The range [${range.start}, ${range.end}) is outside block ${block.blockId}.`,
      );
    }
    marks.fill(mark, start, end);
  }
}

function toSegments(text: string, marks: (Mark | null)[]): Segment[] {
  const segments: Segment[] = [];
  let start = 0;
  for (let i = 1; i <= text.length; i++) {
    if (i === text.length || marks[i] !== marks[start]) {
      segments.push({ text: text.slice(start, i), mark: marks[start] });
      start = i;
    }
  }
  return segments;
}