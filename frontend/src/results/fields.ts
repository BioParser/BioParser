import type { EtsRow } from "../api/client";
import { ETS_COLUMNS } from "./etsColumns";
import { namedFields } from "./warnings";

export type EtsColumn = (typeof ETS_COLUMNS)[number];

// A readable label for each ETS column. The type makes a new column fail to compile until it
// has a label, and etsColumns.test.ts makes a new API column fail until ETS_COLUMNS lists it.
export const FIELD_LABELS: Record<EtsColumn, string> = {
  traitID: "Trait ID",
  traitName: "Trait name",
  verbatimTraitName: "Trait as printed",
  scientificName: "Scientific name",
  verbatimScientificName: "Name as printed",
  taxonID: "Taxon ID",
  traitValue: "Trait value",
  verbatimTraitValue: "Value as printed",
  traitUnit: "Unit",
  verbatimTraitUnit: "Unit as printed",
  taxonRank: "Taxon rank",
  kingdom: "Kingdom",
  phylum: "Phylum",
  class: "Class",
  order: "Order",
  family: "Family",
  genus: "Genus",
  sex: "Sex",
  lifeStage: "Life stage",
  age: "Age",
  morphotype: "Morphotype",
  basisOfRecord: "Basis of record",
  basisOfRecordDescription: "Basis of record description",
  measurementMethod: "Measurement method",
  measurementResolution: "Measurement resolution",
  measurementRemarks: "Measurement remarks",
  measurementDeterminedBy: "Measured by",
  measurementDeterminedDate: "Measurement date",
  aggregateMeasure: "Aggregate measure",
  statisticalMethod: "Statistical method",
  individualCount: "Individual count",
  dispersion: "Dispersion",
  measurementValue_min: "Minimum",
  measurementValue_max: "Maximum",
  measurementAccuracy: "Measurement accuracy",
  measurementID: "Measurement ID",
  occurrenceID: "Occurrence ID",
  warnings: "Warnings",
  references: "References",
  eventID: "Event ID",
  preparations: "Preparations",
  samplingProtocol: "Sampling protocol",
  year: "Year",
  month: "Month",
  day: "Day",
  eventDate: "Event date",
  locationID: "Location ID",
  habitat: "Habitat",
  decimalLongitude: "Longitude",
  decimalLatitude: "Latitude",
  elevation: "Elevation",
  geodeticDatum: "Geodetic datum",
  verbatimLocality: "Locality as printed",
  country: "Country",
  countryCode: "Country code",
  occurrenceRemarks: "Occurrence remarks",
};

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
// adapter leaves empty, still shows where it belongs. `warnings` is left out: the Flag column
// lists the codes, and each field shows the codes that name it.
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
