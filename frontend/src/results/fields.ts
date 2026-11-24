import type { EtsRow } from "../api/client";
import { ETS_COLUMNS } from "./etsColumns";
import { type EtsColumn, FIELD_LABELS } from "./fieldLabels";
import { namedFields } from "./warnings";

export interface Field {
  column: EtsColumn;
  label: string;
  // The value as text, or null for an empty column that a warning names.
  value: string | null;
  // The warning codes that name this column, in the row's order.
  codes: string[];
}

// The row's fields in ETS column order: every column with a value, and an empty column when a
// warning names it, so a warning such as not_in_vocabulary:basisOfRecord, whose column the
// adapter leaves empty, still shows where it belongs. `warnings` is left out: each field shows
// the codes that name it, and otherCodes() lists the rest.
export function rowFields(row: EtsRow, codes: readonly string[]): Field[] {
  const byColumn = new Map<string, string[]>();
  for (const code of codes) {
    for (const column of namedFields(code)) {
      const named = byColumn.get(column);
      if (named === undefined) {
        byColumn.set(column, [code]);
      } else if (!named.includes(code)) {
        named.push(code);
      }
    }
  }
  const fields: Field[] = [];
  for (const column of ETS_COLUMNS) {
    if (column === "warnings") {
      continue;
    }
    const value = fieldText(row[column]);
    const named = byColumn.get(column) ?? [];
    if (value !== null || named.length > 0) {
      fields.push({ column, label: FIELD_LABELS[column], value, codes: named });
    }
  }
  return fields;
}

// The row's codes that name none of its fields, each once: a code without a column, or one
// that names only columns ETS does not have. The field list shows them after the fields, so
// every code of the row has a place.
export function otherCodes(fields: readonly Field[], codes: readonly string[]): string[] {
  const placed = new Set(fields.flatMap((field) => field.codes));
  return [...new Set(codes)].filter((code) => !placed.has(code));
}

// aggregateMeasure is the only column that is not text.
function fieldText(value: string | boolean | null | undefined): string | null {
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (value == null || value.trim() === "") {
    return null;
  }
  return value;
}
