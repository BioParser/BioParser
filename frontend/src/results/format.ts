import type { EtsRow } from "../api/client";

// The taxon as shown: the accepted name, or the printed name when there is none.
export function taxonText(row: EtsRow): string {
  return row.scientificName ?? row.verbatimScientificName;
}

// The printed value with the printed unit. A paper that prints only a range has no single
// value, so the minimum and the maximum are shown instead (#340: they share the unit).
export function reportedText(row: EtsRow): string | null {
  const value =
    row.verbatimTraitValue ?? rangeText(row.measurementValue_min, row.measurementValue_max);
  return withUnit(value, row.verbatimTraitUnit);
}

function rangeText(min: string | null | undefined, max: string | null | undefined): string | null {
  if (min != null && max != null) return `${min}–${max}`;
  if (min != null) return `min ${min}`;
  if (max != null) return `max ${max}`;
  return null;
}

function withUnit(value: string | null, unit: string | null | undefined): string | null {
  if (value === null) return null;
  return unit != null && unit !== "" ? `${value} ${unit}` : value;
}
