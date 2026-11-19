// The ETS `warnings` column is one string. format_warning in src/bioparser/ets/notices.py
// writes each code as `kind:field`, and to_ets in src/bioparser/ets/adapter.py joins them
// with " | ". A row without warnings has an empty string.

export function parseWarnings(warnings: string): string[] {
  return warnings
    .split("|")
    .map((code) => code.trim())
    .filter((code) => code !== "");
}

// Plain-language names for the fields a warning can name.
const FIELD_LABELS: Record<string, string> = {
  scientificName: "The scientific name",
  individualCount: "The individual count",
  age: "The age",
  measurementValue_min: "The minimum",
  measurementValue_max: "The maximum",
  dispersion: "The dispersion",
  year: "The year",
  month: "The month",
  day: "The day",
  decimalLongitude: "The longitude",
  decimalLatitude: "The latitude",
  elevation: "The elevation",
  sex: "The sex",
  basisOfRecord: "The basis of record",
  aggregateMeasure: "The aggregate measure",
  countryCode: "The country code",
  measurementDeterminedDate: "The measurement date",
  eventDate: "The event date",
};

// Each kind with the fields it can name, as the Notice models in notices.py define them.
// A code outside this table is unknown, and the UI shows it as it is (#290 catalogues codes).
const KINDS: Record<string, { fields: readonly string[]; text: (label: string) => string }> = {
  not_numeric: {
    fields: [
      "individualCount",
      "age",
      "measurementValue_min",
      "measurementValue_max",
      "dispersion",
      "year",
      "month",
      "day",
      "decimalLongitude",
      "decimalLatitude",
      "elevation",
    ],
    text: (label) => `${label} is not a number. The printed text is kept.`,
  },
  out_of_range: {
    fields: [
      "year",
      "month",
      "day",
      "decimalLongitude",
      "decimalLatitude",
      "individualCount",
      "dispersion",
    ],
    text: (label) => `${label} is outside the range of its column. The printed number is kept.`,
  },
  not_verified: {
    fields: ["scientificName"],
    text: (label) => `${label} is the model's reading. No taxonomy lookup has confirmed it.`,
  },
  not_in_vocabulary: {
    fields: ["sex", "basisOfRecord", "aggregateMeasure", "countryCode"],
    text: (label) => `${label} is not one of the accepted values.`,
  },
  not_a_date: {
    fields: ["measurementDeterminedDate", "eventDate"],
    text: (label) => `${label} is not a calendar day. The printed text is kept.`,
  },
};

// Codes whose field names several columns.
const COMBINED: Record<string, string> = {
  "not_a_date:year,month,day": "The year, month, and day together are not a calendar day.",
  "bounds_reversed:measurementValue_min,measurementValue_max":
    "The minimum is greater than the maximum. Both are kept as printed.",
};

// The plain-language line for a known code, or null for an unknown one.
export function warningText(code: string): string | null {
  if (Object.hasOwn(COMBINED, code)) {
    return COMBINED[code];
  }
  const separator = code.indexOf(":");
  if (separator < 0) {
    return null;
  }
  const kind = code.slice(0, separator);
  const field = code.slice(separator + 1);
  const known = Object.hasOwn(KINDS, kind) ? KINDS[kind] : undefined;
  if (known === undefined || !known.fields.includes(field)) {
    return null;
  }
  return known.text(FIELD_LABELS[field]);
}
