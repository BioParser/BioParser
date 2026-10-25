# Extraction artifact

The extraction artifact is the file an extractor writes for one parsed paper. It holds the measurements the extractor found, each tied to the text it came from. The ETS adapter ([ets-adapter.md](ets-adapter.md)) reads this file to build the rows a user is shown.

## Principles

- The artifact stores what the paper said. A field is the printed text, or a value read from printed text.
- Every text is grounded. A citation always has a range in the parsed text, so any field can be traced to its source.
- The artifact checks shape only. It does not decide whether a measurement is good enough to show. An unresolved name or a unit that cannot be converted is still stored, because it is grounded. The adapter decides what happens to it.
- The artifact does not contain ETS columns that the adapter derives. It does not store the standardized value, the taxon id, or the trait id.
- The artifact is independent of the ETS version. The adapter maps artifact fields to the columns of the version it targets.

## The file

One observation is one measurement, which becomes one ETS row. The artifact records the extractor name and version once, for all observations.

```json
{
  "schema_version": "1",
  "extractor": "default",
  "extractor_version": "1",
  "observations": [
    {
      "trait": {
        "kind": "interpretation",
        "value": "body_mass",
        "passage": {
          "kind": "citation",
          "text": "Mus musculus weighed 12.5 kg",
          "evidence": [{"block_id": "b0", "start": 0, "end": 28}]
        }
      },
      "verbatimScientificName": {
        "kind": "citation",
        "text": "Mus musculus",
        "evidence": [{"block_id": "b0", "start": 0, "end": 12}]
      },
      "verbatimTraitValue": {
        "kind": "citation",
        "text": "12.5",
        "evidence": [{"block_id": "b0", "start": 21, "end": 25}]
      },
      "verbatimTraitUnit": {
        "kind": "citation",
        "text": "kg",
        "evidence": [{"block_id": "b0", "start": 26, "end": 28}]
      }
    }
  ]
}
```

Every other field is optional and is `null` when the observation has no value for it. The example omits them.

## Field shapes

There are three shapes. The field table below says which shapes each field accepts.

### Citation

A citation stores a printed string and the ranges where it was located.

```json
{"kind": "citation", "text": "12.5", "evidence": [{"block_id": "b0", "start": 21, "end": 25}]}
```

- `text` is printed text. On a field it is the narrow token, such as `12.5` or `kg`. As the passage of an interpretation it is the wider text the value was read from.
- `evidence` is required and has at least one range.
- A range is half-open, `[start, end)`, in one parser block. `start` and `end` are Unicode code points, the same indexes as a Python `str`, not UTF-16 code units. `end` is greater than `start`.
- One range is the usual case. A second range is a paragraph that continues in the next column.
- The ranges follow the parser's block order, and `text` is the concatenation of their slices.

### Interpretation

An interpretation stores a `value` that is read from a passage rather than printed.

```json
{
  "kind": "interpretation",
  "value": "12",
  "passage": {
    "kind": "citation",
    "text": "8 males and 4 females",
    "evidence": [{"block_id": "b1", "start": 40, "end": 61}]
  }
}
```

- `value` is any non-empty string. The passage is a citation, and it grounds the value.
- The value is not the slice. The artifact does not look the value up in the passage.
- Examples: `8 males and 4 females` read as `12`, `8 Mar 1963` read as `1963-03-08`, `Germany` read as `DE`, `M. musculus` read as `Mus musculus`.

### Correction

A correction keeps a narrow citation beside an interpretation of a wider passage.

```json
{
  "kind": "correction",
  "citation": {"kind": "citation", "text": "adult", "evidence": [{"block_id": "b0", "start": 3, "end": 8}]},
  "interpretation": {
    "kind": "interpretation",
    "value": "adult",
    "passage": {"kind": "citation", "text": "adult males", "evidence": [{"block_id": "b0", "start": 3, "end": 14}]}
  }
}
```

A correction is for a free-text column where the printed token is usable but not the best value. For example, a printed `juv.` can carry the reading `juvenile`, and a printed `U.S.` can carry `United States`.

- The adapter uses the interpretation value for the column. Without a correction it uses the printed citation.
- The narrow citation stays for tracing and for UI highlighting, where its precise range is better than the whole passage.
- Both are grounded. The citation points at the printed token, and the interpretation's passage points at the wider text the value was read from.

## Field table

Each field has one of four shapes. The shape says which kinds of value it accepts.

| Shape | Accepts | Meaning |
|---|---|---|
| **printed** | Citation only. | The printed token is the value. |
| **printed + correction** | Citation, or correction. | A free-text column. The printed token is always usable. A correction keeps it beside a better value read from wider text. |
| **printed or interpreted** | Citation, or interpretation. | The column has a format check. The printed token is a citation if it passes the check. Otherwise the value is an interpretation. |
| **interpreted** | Interpretation only. | The paper does not print the value, such as `LivingSpecimen` or `TRUE`. |

### Choosing a shape

Ask these questions in order for a field.

1. **Is it a required verbatim column?** (`verbatimScientificName`, `verbatimTraitValue`, `verbatimTraitUnit`.) It is **printed**. It is the printed token by definition.
2. **Does the paper ever print the value itself?** If not, it is **interpreted**. `basisOfRecord` is `LivingSpecimen`, and a paper does not print that.
3. **Does the ETS column check its format?** A number, a date, a code, or a fixed list of tokens is a format check.
   - If it does, the field is **printed or interpreted**. Printed text often fails the check, so the field is either the printed token (when it passes) or a value read from a passage.
   - If the column is free text, the field is **printed + correction**.

The difference between the last two is what to do with a wider passage. Take two fields from the same sentence, `Adult males weighed 12 g, and 8 males and 4 females were caught`:

- `lifeStage` is free text. The printed token `Adult` is a valid value. A correction can keep `Adult` as the citation, and add an interpretation `adult` whose passage is `Adult males`. Both are valid text, so the adapter can use either.
- `individualCount` must be a number. The passage `8 males and 4 females` is not a number, so it cannot sit beside the field as a fallback. The field is an interpretation, `12`, with that passage as its grounding. If the paper had printed `12`, the field would be a citation instead.

So a passage is only a usable fallback in a free-text column. In a column with a format check, it is only the grounding for a value.

Three fields are the exception to question 2: `scientificName`, `traitValue`, and `traitUnit`. Each is an **interpreted** value that sits beside a required verbatim column, and it is omitted when the printed token is already the value. The verbatim column holds what was printed, and the paired field holds the standardized reading, such as `Mus musculus` for a printed `M. musculus`.

In the table below, the ETS term is the column the adapter fills from the field. A `-` means the field is not an ETS column. The Notes column explains a shape when it is not obvious.

| Field | Shape | Required | ETS term | Notes |
|---|---|---|---|---|
| `trait` | interpreted | yes | - | A free string such as `body_mass`. The adapter looks it up in the trait thesaurus. |
| `verbatimTraitName` | printed + correction | | `verbatimTraitName` | |
| `verbatimScientificName` | printed | yes | `verbatimScientificName` | The printed name, such as `M. musculus`. |
| `scientificName` | interpreted | | `scientificName` | Paired with `verbatimScientificName`. A name read from the printed citation, such as `Mus musculus`. |
| `verbatimTraitValue` | printed | yes | `verbatimTraitValue` | |
| `traitValue` | interpreted | | `traitValue` | Paired with `verbatimTraitValue`. A cleaned reading of the printed number, in the unit the paper printed. |
| `verbatimTraitUnit` | printed | yes | `verbatimTraitUnit` | |
| `traitUnit` | interpreted | | `traitUnit` | Paired with `verbatimTraitUnit`. A cleaned reading of the printed unit, such as `kg` for `kilograms`. |
| `taxonRank` | printed + correction | | `taxonRank` | |
| `kingdom` | printed + correction | | `kingdom` | |
| `phylum` | printed + correction | | `phylum` | |
| `taxonClass` | printed + correction | | `class` | Taxonomic class. Named `taxonClass` because `class` is a Python keyword. The ETS column is `class`. |
| `order` | printed + correction | | `order` | |
| `family` | printed + correction | | `family` | |
| `genus` | printed + correction | | `genus` | |
| `sex` | printed or interpreted | | `sex` | Token list (`male`, `female`, `subadult`, `unknown`, `hermaphrodite`). A paper prints `males`, `M`, or `♂`, which fail the check. Compare `lifeStage`, which is free text. |
| `lifeStage` | printed + correction | | `lifeStage` | Free text, so the printed token is already a usable value. Compare `age`. |
| `age` | printed or interpreted | | `age` | A number. A paper prints `3 years` or `adult`, which fail the check. `adult` belongs in `lifeStage`. |
| `morphotype` | printed + correction | | `morphotype` | |
| `basisOfRecord` | interpreted | | `basisOfRecord` | Token list. A paper does not print `LivingSpecimen`, so there is no citation form. |
| `basisOfRecordDescription` | printed + correction | | `basisOfRecordDescription` | |
| `measurementMethod` | printed + correction | | `measurementMethod` | |
| `measurementResolution` | printed + correction | | `measurementResolution` | |
| `measurementRemarks` | printed + correction | | `measurementRemarks` | |
| `measurementDeterminedBy` | printed + correction | | `measurementDeterminedBy` | |
| `measurementDeterminedDate` | printed or interpreted | | `measurementDeterminedDate` | A date. A paper prints `8 Mar 1963`, which fails the check. |
| `aggregateMeasure` | interpreted | | `aggregateMeasure` | `TRUE` or `FALSE`. A paper does not print it. It is read from wording such as `mean of 12`. |
| `statisticalMethod` | printed + correction | | `statisticalMethod` | The printed phrase, such as `mean ± SD`. When it is omitted, the adapter writes a phrase from `centralKind` and, when a dispersion number was stored, from `dispersionKind`. |
| `centralKind` | interpreted | | - | `mean` or `median`. A paper does not print this token. It is not converted. |
| `dispersionKind` | interpreted | | - | `standardDeviation`, `variance`, or `standardError`. With `dispersion`, it selects the conversion. |
| `individualCount` | printed or interpreted | | `individualCount` | A number. A paper prints `8 males and 4 females`, read as `12`. |
| `dispersion` | printed or interpreted | | `dispersion` | A number. The printed token often carries a sign or a label, such as `± 1.2`. It shares the trait unit. There is no separate dispersion unit. |
| `measurementValueMin` | printed or interpreted | | `measurementValue_min` | A number. Often read from a range such as `10-14 g`. It shares the trait unit. |
| `measurementValueMax` | printed or interpreted | | `measurementValue_max` | A number. Often read from a range such as `10-14 g`. It shares the trait unit. |
| `measurementAccuracy` | printed + correction | | `measurementAccuracy` | |
| `occurrenceID` | printed + correction | | `occurrenceID` | |
| `eventID` | printed + correction | | `eventID` | |
| `preparations` | printed + correction | | `preparations` | |
| `samplingProtocol` | printed + correction | | `samplingProtocol` | |
| `year` | printed or interpreted | | `year` | A number. Stored apart from `month`, `day`, and `eventDate`, because a paper may print only some. |
| `month` | printed or interpreted | | `month` | |
| `day` | printed or interpreted | | `day` | |
| `eventDate` | printed or interpreted | | `eventDate` | A date. `8 Mar 1963` is read as `1963-03-08`. |
| `locationID` | printed + correction | | `locationID` | |
| `habitat` | printed + correction | | `habitat` | |
| `decimalLongitude` | printed or interpreted | | `decimalLongitude` | A number. A paper prints `41° S` or degrees and minutes, read as a decimal. |
| `decimalLatitude` | printed or interpreted | | `decimalLatitude` | A number. A paper prints `41° S` or degrees and minutes, read as a decimal. |
| `elevation` | printed or interpreted | | `elevation` | A number. A paper prints a unit, such as `1,200 m`. |
| `geodeticDatum` | printed + correction | | `geodeticDatum` | |
| `verbatimLocality` | printed + correction | | `verbatimLocality` | |
| `country` | printed + correction | | `country` | Free text. The printed name is the value. Compare `countryCode`. |
| `countryCode` | printed or interpreted | | `countryCode` | ISO 3166-1 alpha-2. A paper prints `Germany`, read as `DE`. |
| `occurrenceRemarks` | printed + correction | | `occurrenceRemarks` | |

Not in the artifact, because the adapter derives or assigns them: `taxonID`, `traitName`, `traitID`, `measurementID`, `warnings`, and `references`.

## Names and values

Field names are the artifact's own, in camelCase, and follow the ETS terms where there is one. `taxonClass` and `measurementValueMin` differ from the ETS spellings `class` and `measurementValue_min`. A new ETS version changes the adapter's mapping and not the stored artifact.

The artifact does not enforce a set of allowed values. An interpretation `value` is a non-empty string. That includes `trait`, `sex`, `basisOfRecord`, `aggregateMeasure`, `centralKind`, `dispersionKind`, and `traitUnit`. An extractor may constrain its model to a set, or check afterwards. The adapter decides what a value means and warns on one it does not know.

## What the worker checks

The worker validates every artifact before it stores one, whichever extractor produced it. A failure fails the job with `extraction_failed`, and nothing is stored.

1. The artifact matches the schema.
2. It names the extractor and version the job runs.
3. Every range is exactly the slice it names. The block must exist, the ranges of one citation must be in reading order, and `text` must be the joined slices.

The schema does not open the parsed text, so check 3 is done by the worker.

## Writing an extractor

- Return the whole artifact. An extractor returns an `ExtractionArtifact` the way a parser returns a `ParserArtifact`.
- Ground every field. A field you cannot locate is not stored. If the field is required, drop the observation.
- Keep failed attempts out of the artifact. What the model tried and could not ground belongs in the extractor's logs.
- Keep the number and the unit as the paper printed them. `traitValue`, when stored, is a cleaned reading of the printed number. `traitUnit`, when stored, is a cleaned reading of the printed unit, such as `kg` for `kilograms`. The adapter converts that pair into grams or millimetres. Do not store `12500` and `g` for a printed `12.5 kg`.
- Record `centralKind` and `dispersionKind` when the paper summarizes a measurement. The adapter uses `dispersionKind` to convert `dispersion`, and it converts a stored minimum and maximum with the same factor as the trait value. One unit covers the measurement, and it is the unit the paper printed for the trait value. Do not store a separate unit for the minimum, the maximum, or the dispersion. If a paper mixes units, store those numbers in that printed unit.
- You may be stricter than the artifact. An extractor may check names or repair a field before it stores a result. It may also store a result the adapter later rejects, such as a unit that does not fit the trait.
- The artifact does not record how a range was found. An extractor may find one any way it likes.
