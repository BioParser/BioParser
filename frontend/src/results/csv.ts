import type { EtsRow } from "../api/client";
import { ETS_COLUMNS } from "./etsColumns";

// RFC 4180: records end with CRLF, and a field that holds a comma, a double quote, CR or LF
// is wrapped in double quotes, with each double quote inside it doubled.
const CRLF = "\r\n";
const NEEDS_QUOTES = /[",\r\n]/;

export function csvField(value: string | boolean | null | undefined): string {
  if (value === null || value === undefined) {
    return "";
  }
  const text = String(value);
  return NEEDS_QUOTES.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

// One header line with the ETS column names, then one line per row, in the order given.
// Values are written as the API returned them: `warnings` keeps its " | " separators, and
// aggregateMeasure is written as true or false.
export function toCsv(rows: readonly EtsRow[]): string {
  const lines = [ETS_COLUMNS.join(",")];
  for (const row of rows) {
    lines.push(ETS_COLUMNS.map((column) => csvField(row[column])).join(","));
  }
  return lines.join(CRLF) + CRLF;
}
