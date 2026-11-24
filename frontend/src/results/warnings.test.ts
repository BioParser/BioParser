import { describe, expect, it } from "vitest";
import { flagsRow, informationalLabel, namedFields, parseWarnings, warningText } from "./warnings";

describe("parseWarnings", () => {
  it("reads no codes from an empty string", () => {
    expect(parseWarnings("")).toEqual([]);
  });

  it("reads one code", () => {
    expect(parseWarnings("not_verified:scientificName")).toEqual(["not_verified:scientificName"]);
  });

  it("splits the adapter's ' | ' separator and keeps commas inside a code", () => {
    expect(
      parseWarnings("not_verified:scientificName | not_a_date:year,month,day | not_numeric:age"),
    ).toEqual(["not_verified:scientificName", "not_a_date:year,month,day", "not_numeric:age"]);
  });

  it("also accepts '|' without spaces, as the issue describes it", () => {
    expect(parseWarnings("not_numeric:age|out_of_range:month")).toEqual([
      "not_numeric:age",
      "out_of_range:month",
    ]);
  });
});

describe("namedFields", () => {
  it("reads the column after the colon", () => {
    expect(namedFields("not_numeric:age")).toEqual(["age"]);
  });

  it("reads each column of a code that names several", () => {
    expect(namedFields("not_a_date:year,month,day")).toEqual(["year", "month", "day"]);
  });

  it("reads the columns of a kind the UI does not know", () => {
    expect(namedFields("new_kind:habitat")).toEqual(["habitat"]);
  });

  it("reads no column from a code without one", () => {
    expect(namedFields("no_colon")).toEqual([]);
    expect(namedFields("kind:")).toEqual([]);
  });
});

describe("warningText", () => {
  it.each([
    ["not_verified:scientificName", "The scientific name is the model's reading."],
    ["not_numeric:individualCount", "The individual count is not a number."],
    ["not_numeric:measurementValue_min", "The measurement value min is not a number."],
    ["not_numeric:decimalLongitude", "The decimal longitude is not a number."],
    ["out_of_range:decimalLatitude", "The decimal latitude is outside the range of its column."],
    [
      "not_in_vocabulary:sex",
      "The sex is not one of the accepted values. The printed text is kept.",
    ],
    [
      "not_in_vocabulary:countryCode",
      "The country code is not one of the accepted values. The printed text is kept.",
    ],
    [
      "not_in_vocabulary:basisOfRecord",
      "The basis of record is not one of the accepted values. The column is left empty.",
    ],
    [
      "not_in_vocabulary:aggregateMeasure",
      "The aggregate measure is not one of the accepted values. The column is left empty.",
    ],
    ["not_a_date:eventDate", "The event date is not a calendar day."],
    ["not_a_date:year,month,day", "The year, month, and day together are not a calendar day."],
    ["ambiguous_date:eventDate", "The event date can be read day first or month first."],
    [
      "ambiguous_date:measurementDeterminedDate",
      "The measurement determined date can be read day first",
    ],
    [
      "bounds_reversed:measurementValue_min,measurementValue_max",
      "The measurement value min is greater than the measurement value max.",
    ],
  ])("explains %s", (code, start) => {
    expect(warningText(code)).toContain(start);
  });

  it.each([
    "unmapped:traitName",
    "not_numeric:sex", // a known kind with a field it never names
    "out_of_range:elevation",
    "no-colon",
    "toString:x", // not an own property of the table
    "",
  ])("has no text for the unknown code %j", (code) => {
    expect(warningText(code)).toBeNull();
  });
});

describe("flagsRow", () => {
  it("does not flag a row for an unverified name", () => {
    expect(flagsRow("not_verified:scientificName")).toBe(false);
    expect(informationalLabel("not_verified:scientificName")).toBe("Name not verified");
  });

  it.each([
    "not_numeric:age",
    "out_of_range:month",
    "not_in_vocabulary:sex",
    "not_a_date:eventDate",
    "ambiguous_date:eventDate",
    "not_a_date:year,month,day",
    "bounds_reversed:measurementValue_min,measurementValue_max",
    "a_new_kind:age", // an unknown code flags, so a new backend code is not hidden
    "toString", // not an own property of the table
  ])("flags a row for %j", (code) => {
    expect(flagsRow(code)).toBe(true);
    expect(informationalLabel(code)).toBeNull();
  });
});
