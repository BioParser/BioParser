import { describe, expect, it } from "vitest";
import type { EtsRow } from "../api/client";
import { ETS_COLUMNS } from "./etsColumns";
import { FIELD_LABELS } from "./fieldLabels";
import { otherCodes, rowFields } from "./fields";

function row(overrides: Partial<EtsRow> = {}): EtsRow {
  return {
    verbatimTraitName: "body mass",
    verbatimScientificName: "M. musculus",
    measurementID: "1",
    warnings: "",
    references: "abc",
    ...overrides,
  };
}

describe("FIELD_LABELS", () => {
  it("has a distinct label for every ETS column", () => {
    const labels = ETS_COLUMNS.map((column) => FIELD_LABELS[column]);
    expect(labels.every((label) => label.length > 0)).toBe(true);
    expect(new Set(labels).size).toBe(ETS_COLUMNS.length);
  });

  // A curator matches the label with the CSV header, so it keeps every word of the term.
  it.each(ETS_COLUMNS)("labels %s with its own term, split into words", (column) => {
    const words = (text: string) => text.replace(/[\s_]/g, "").toLowerCase();
    expect(words(FIELD_LABELS[column])).toBe(words(column));
  });
});

describe("rowFields", () => {
  it("lists the filled columns in ETS column order, without the warnings column", () => {
    const fields = rowFields(row({ occurrenceRemarks: "last", traitID: "first" }), []);
    expect(fields.map((field) => field.column)).toEqual([
      "traitID",
      "verbatimTraitName",
      "verbatimScientificName",
      "measurementID",
      "references",
      "occurrenceRemarks",
    ]);
    expect(fields[0]).toEqual({ column: "traitID", label: "Trait ID", value: "first", codes: [] });
  });

  it("leaves out null, empty and blank columns", () => {
    const fields = rowFields(row({ sex: null, lifeStage: "", habitat: " \n " }), []);
    expect(fields.map((field) => field.column)).not.toContain("sex");
    expect(fields.map((field) => field.column)).not.toContain("lifeStage");
    expect(fields.map((field) => field.column)).not.toContain("habitat");
  });

  it("writes aggregateMeasure as Yes or No, and keeps false", () => {
    const value = (aggregateMeasure: boolean) =>
      rowFields(row({ aggregateMeasure }), []).find((field) => field.column === "aggregateMeasure")
        ?.value;
    expect(value(true)).toBe("Yes");
    expect(value(false)).toBe("No");
  });

  it("gives each column the codes that name it, and keeps an empty named column", () => {
    const codes = ["not_numeric:age", "not_a_date:year,month,day", "not_numeric:age"];
    const fields = rowFields(row({ age: "about 3", year: "2001" }), codes);
    const byColumn = new Map(fields.map((field) => [field.column, field]));
    // A code repeated in the row is shown once.
    expect(byColumn.get("age")?.codes).toEqual(["not_numeric:age"]);
    expect(byColumn.get("year")).toMatchObject({ value: "2001", codes: [codes[1]] });
    expect(byColumn.get("month")).toMatchObject({ value: null, codes: [codes[1]] });
    expect(byColumn.get("day")).toMatchObject({ value: null, codes: [codes[1]] });
  });

  it("ignores a code that names no ETS column", () => {
    const fields = rowFields(row(), ["new_kind:notAColumn", "no_colon"]);
    expect(fields.every((field) => field.codes.length === 0)).toBe(true);
    expect(fields).toHaveLength(4);
  });
});

describe("otherCodes", () => {
  it("keeps the codes that name none of the listed fields, each once", () => {
    const codes = [
      "not_numeric:age",
      "no_colon",
      "new_kind:notAColumn",
      "no_colon",
      "new_kind:warnings", // the warnings column is not listed
    ];
    const fields = rowFields(row(), codes);
    expect(otherCodes(fields, codes)).toEqual([
      "no_colon",
      "new_kind:notAColumn",
      "new_kind:warnings",
    ]);
  });

  it("is empty when every code names a listed field", () => {
    const codes = ["not_numeric:age", "not_a_date:year,month,day"];
    expect(otherCodes(rowFields(row(), codes), codes)).toEqual([]);
  });
});
