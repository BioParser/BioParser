import { describe, expect, it } from "vitest";
import type { EtsRow } from "../api/client";
import { reportedText, taxonText } from "./format";

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

describe("taxonText", () => {
  it("prefers scientificName and falls back to the printed name", () => {
    expect(taxonText(row({ scientificName: "Mus musculus" }))).toBe("Mus musculus");
    expect(taxonText(row({ scientificName: null }))).toBe("M. musculus");
  });
});

describe("reportedText", () => {
  it("joins the printed value and unit", () => {
    expect(reportedText(row({ verbatimTraitValue: "21.5", verbatimTraitUnit: "g" }))).toBe(
      "21.5 g",
    );
    expect(reportedText(row({ verbatimTraitValue: "3" }))).toBe("3");
  });

  it("shows a printed range when there is no single value", () => {
    expect(
      reportedText(
        row({ measurementValue_min: "10", measurementValue_max: "14", verbatimTraitUnit: "g" }),
      ),
    ).toBe("10–14 g");
    expect(reportedText(row({ measurementValue_min: "10" }))).toBe("min 10");
    expect(reportedText(row({ measurementValue_max: "14" }))).toBe("max 14");
  });

  it("is null when nothing was printed", () => {
    expect(reportedText(row())).toBeNull();
  });
});
