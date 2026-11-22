import type { components } from "../api/schema";

type Schemas = components["schemas"];
export type TextLine = Schemas["TextLine"];
type BoundingBox = Schemas["BoundingBox"];

// One box per line that the half-open range [start, end) touches, from the first to the
// last inked glyph edge inside the range, with the line's shared top and bottom. A line
// whose characters in the range have no ink (a space, a dropped hyphen, MinerU's ^{ and }
// markers) gives no box. Indexes are code points into the block text, like the evidence
// ranges. This is boxes_for_range in src/bioparser/parser/models.py.
export function boxesForRange(
  lines: readonly TextLine[],
  start: number,
  end: number,
): BoundingBox[] {
  if (end <= start) {
    return [];
  }
  const boxes: BoundingBox[] = [];
  for (const line of lines) {
    const lo = Math.max(start, line.start);
    const hi = Math.min(end, line.end);
    let left = Number.POSITIVE_INFINITY;
    let right = Number.NEGATIVE_INFINITY;
    for (let index = lo - line.start; index < hi - line.start; index++) {
      const x0 = line.x0[index];
      const x1 = line.x1[index];
      if (x0 == null || x1 == null) {
        continue;
      }
      left = Math.min(left, x0);
      right = Math.max(right, x1);
    }
    if (left <= right) {
      boxes.push({ x0: left, y0: line.y0, x1: right, y1: line.y1 });
    }
  }
  return boxes;
}
