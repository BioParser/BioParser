import { describe, expect, it } from "vitest";
import { parseWarnings, warningText } from "./warnings";

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

describe("warningText", () => {
  it.each([
    ["not_verified:scientificName", "The scientific name is the model's reading."],
    ["not_numeric:individualCount", "The individual count is not a number."],
    ["out_of_range:decimalLatitude", "The latitude is outside the range of its column."],
    ["not_in_vocabulary:sex", "The sex is not one of the accepted values."],
    ["not_a_date:eventDate", "The event date is not a calendar day."],
    ["not_a_date:year,month,day", "The year, month, and day together are not a calendar day."],
    [
      "bounds_reversed:measurementValue_min,measurementValue_max",
      "The minimum is greater than the maximum.",
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
