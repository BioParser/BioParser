import { describe, expect, it } from "vitest";
import type { EtsRow } from "../api/client";
import { csvField, toCsv } from "./csv";
import { ETS_COLUMNS } from "./etsColumns";

function row(overrides: Partial<EtsRow> = {}): EtsRow {
  return {
    verbatimTraitName: "body mass",
    verbatimScientificName: "M. musculus",
    measurementID: "1",
    warnings: "",
    references: "a".repeat(64),
    ...overrides,
  };
}

describe("csvField", () => {
  it.each([
    ["plain", "plain"],
    ["a,b", '"a,b"'],
    ['said "big"', '"said ""big"""'],
    ["line 1\nline 2", '"line 1\nline 2"'],
    ["cr\rlf", '"cr\rlf"'],
    ["Ålandsö µm", "Ålandsö µm"],
    ["", ""],
  ])("writes %j as %j", (value, expected) => {
    expect(csvField(value)).toBe(expected);
  });

  it("writes null and a missing value as an empty field, and booleans as words", () => {
    expect(csvField(null)).toBe("");
    expect(csvField(undefined)).toBe("");
    expect(csvField(true)).toBe("true");
    expect(csvField(false)).toBe("false");
  });
});

describe("toCsv", () => {
  it("writes the header in API order and one CRLF line per row", () => {
    const csv = toCsv([row(), row({ measurementID: "2" })]);
    const lines = csv.split("\r\n");
    expect(lines[0]).toBe(ETS_COLUMNS.join(","));
    expect(lines).toHaveLength(4); // header, two rows, and the empty string after the last CRLF
    expect(lines[3]).toBe("");
  });

  it("writes only the header for an empty result", () => {
    expect(toCsv([])).toBe(`${ETS_COLUMNS.join(",")}\r\n`);
  });

  it("keeps the warnings string as the API returned it", () => {
    const warnings = "not_verified:scientificName | not_a_date:year,month,day";
    const [, line] = toCsv([row({ warnings })]).split("\r\n");
    // The code holds commas, so the field is quoted.
    expect(line).toContain(`"${warnings}"`);
  });

  it("quotes commas, quotes, and newlines so each row parses back to its fields", () => {
    const tricky = row({
      verbatimScientificName: 'Sorex "araneus", juv.',
      measurementRemarks: "first line\r\nsecond line",
      verbatimLocality: "Åland, Suomi",
    });
    // Skip the header line and drop the final CRLF; the CRLF inside the remarks is quoted.
    const record = toCsv([tricky]).slice(ETS_COLUMNS.join(",").length + 2, -2);
    expect(parseRecord(record)).toEqual(ETS_COLUMNS.map((column) => String(tricky[column] ?? "")));
  });
});

// A minimal RFC 4180 reader for one record, so the test checks the round trip.
function parseRecord(record: string): string[] {
  const fields: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < record.length; i++) {
    const char = record[i];
    if (quoted) {
      if (char === '"' && record[i + 1] === '"') {
        field += '"';
        i++;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      fields.push(field);
      field = "";
    } else {
      field += char;
    }
  }
  fields.push(field);
  return fields;
}
