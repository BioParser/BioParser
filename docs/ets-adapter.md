# ETS adapter

BioParser targets the Ecological Trait-data Standard (ETS) v0.10. The vocabulary is published at <https://ecologicaltraitdata.github.io/ETS/>.

The ETS adapter turns an extraction artifact ([extraction-artifact.md](extraction-artifact.md)) into ETS rows. It is the quality gate for what a user is shown. The artifact stores what the paper said, and the adapter decides which of it is good enough to become a row.

## Role

- The adapter reads an artifact and returns rows. It does not modify the artifact and does not write ETS columns back into it.
- It does not call the model, and it does not open the parser artifact.
- One observation is at most one row. An observation the adapter cannot turn into a valid row is dropped.
- A new ETS version changes the adapter's mapping. It does not change the stored artifact.

## Name mapping

The artifact uses its own camelCase names. The adapter maps them to ETS column names in one place. Only these differ:

| Artifact | ETS v0.10 |
|---|---|
| `taxonClass` | `class` |
| `measurementValueMin` | `measurementValue_min` |
| `measurementValueMax` | `measurementValue_max` |

`trait` is not an ETS column. The adapter uses it to look up `traitName`, `traitID`, and the standard unit.

## Sources

The adapter needs three sources. Two are not chosen yet.

- **Taxonomy backbone** for `scientificName` and `taxonID`. The source is chosen later.
- **Trait thesaurus** for `traitName`, `traitID`, and the standard unit. ETS does not define the trait list, so the thesaurus is chosen later. The artifact does not check `trait` against a list. The adapter checks it against the thesaurus.
- **Unit table**, owned by the adapter. It checks that the printed unit belongs to the trait. It converts mass to grams and length to millimetres.

An extractor may store `traitValue` and `traitUnit` itself, for example a converted pair. The adapter uses what is stored. The unit check is not written into the artifact.

## How a row is derived

### Name and taxon

`verbatimScientificName` stays the printed span, including forms such as `M. musculus`. `scientificName`, when stored, is another name a taxonomy lookup can try.

One confident hit becomes the ETS `scientificName` and `taxonID`. No hit, or more than one, leaves `taxonID` empty and adds a warning. The observation is not rewritten.

Until a taxonomy source exists, ETS `scientificName` is the stored `scientificName` when the observation has one, and otherwise the verbatim citation. `taxonID` stays empty. No warning is added for the missing lookup, because none is run.

### Trait value and unit

The adapter resolves the value and the unit each on its own, then converts once.

1. The value is `traitValue` when stored, otherwise the parsed `verbatimTraitValue`.
2. The unit is `traitUnit` when stored, otherwise the printed `verbatimTraitUnit`.
3. The pair is converted with the unit table to grams for mass or millimetres for length. This changes nothing when the unit is already `g` or `mm`.

A stored `traitValue` is in the effective unit, which is `traitUnit` when stored and the printed unit otherwise. An extractor that changes the scale of a value must also store the unit.

A trait outside the unit table drops the row with `trait_unknown`. Until a thesaurus exists, that table is `body_mass`, `body_length`, and `tail_length`. An unknown unit drops the row with `unit_unknown`, and a unit that is not valid for the trait drops it with `unit_not_for_trait`. A value that is not a positive decimal drops the row with `value_not_numeric`.

### Range and dispersion

The minimum, the maximum, and the dispersion share the trait's unit. ETS gives the minimum and the maximum no unit of their own, and it does not convert `dispersion`. The observation records which statistic each number is. That choice selects the factor. A separate unit for those numbers is not stored. One unit covers the measurement.

| Stored choice | ETS column | Conversion |
|---|---|---|
| Trait value | `traitValue`, `traitUnit` | Grams or millimetres |
| `centralKind` `mean` or `median` | No column. It is part of the `statisticalMethod` phrase | None |
| `dispersionKind` `standardDeviation` or `standardError`, with a dispersion number | `dispersion` | The same factor as the trait value |
| `dispersionKind` `variance`, with a dispersion number | `dispersion` | The square of that factor |
| `boundsKind` `range` or `confidenceInterval`, with a minimum and a maximum | `measurementValue_min`, `measurementValue_max` | The same factor as the trait value |

A dispersion kind and a bounds kind can both be set. ETS allows a dispersion metric and a range together.

`statisticalMethod` stays one free-text phrase. A stored phrase is copied. When no phrase is stored, the adapter writes one from `centralKind` and `dispersionKind`. The bounds kind is not part of the phrase, because the minimum and the maximum are their own columns. `mean`, `standardDeviation`, and `range` become `mean and standard deviation`.

- A dispersion number with no usable `dispersionKind` stays in the printed unit, and the row warns `not_converted:dispersion`.
- A `dispersionKind` with no dispersion number warns `missing:dispersion`, and `dispersion` is 0. An unrecognized choice with no number does the same.
- No dispersion number and no kind leaves `dispersion` at 0, with no warning.
- A bounds choice with either side missing warns `missing:measurementValue_min` or `missing:measurementValue_max`. A matched choice still converts the side that is stored.
- A minimum or a maximum with no `boundsKind` stays in the printed unit, with no warning. A single specimen stores none of these choices, so the minimum and the maximum stay empty and `dispersion` is 0.
- An unrecognized bounds choice leaves each stored side in the printed unit and warns `not_converted:measurementValue_min` or `not_converted:measurementValue_max`.
- An unrecognized `centralKind` warns `not_in_vocabulary:statisticalMethod` when the phrase is written from the choices. A stored phrase is copied, and that choice adds no warning.
- A minimum greater than the maximum, after that conversion, drops the row with `bounds_reversed`.

### Citation and interpretation columns

- A free-text column that accepts a citation or a correction uses the interpretation value when the extractor stored a correction, and the printed citation otherwise. The narrow citation of a correction is not used for the column. It stays in the artifact for tracing and for UI highlighting, where a precise range is better than a whole passage.
- A column that accepts a citation or an interpretation (`individualCount`, `measurementValueMin`, dates, coordinates, and the like) is parsed or matched by the adapter. A citation is parsed from its text. An interpretation uses its `value`, and its passage is only the grounding. The adapter parses a number into the numeric ETS column. The artifact does not store the parsed number.
- `sex` and `aggregateMeasure` are matched against the ETS values. A value that does not match adds a warning and leaves the column empty. A `countryCode` that is not two uppercase letters does the same. A `basisOfRecord` that does not match adds a warning and becomes `literatureData`.
- A date is written as `YYYY-MM-DD`. A year-first number or a written month is converted. A numeric day and month is converted when one number is greater than 12, so that number is the day. When both are 12 or less, the date is left empty.

### Defaults and assigned columns

- `basisOfRecord` is `literatureData` when the observation did not record one. `aggregateMeasure` stays empty.
- A missing `individualCount` is `1` and a missing `dispersion` is `0`. ETS defines those defaults.
- `measurementID` is assigned when the rows are built.
- `references` comes from the job's document.
- `warnings` collects what the adapter found while building the row.
- `occurrenceID` is copied when the observation stored one.
- `traitName` and `traitID` stay empty until the thesaurus exists.

## Warnings and drops

A warning means the row was built with something missing or doubtful. A drop means no row was built.

A dropped observation must not disappear silently. The adapter returns, with the rows, a list of drops. Each has the source index of the observation in the artifact and a reason: `trait_unknown`, `unit_unknown`, `unit_not_for_trait`, `value_not_numeric`, or `bounds_reversed`. It also keeps each row's source index, so a row can be traced back to its observation. The index is not derived from `measurementID`, which counts only kept rows.

## Open decisions

- Which of `verbatimScientificName` and `scientificName` the taxonomy lookup tries first.
- The taxonomy source.
- The trait thesaurus.
