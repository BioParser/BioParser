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

Until the taxonomy source exists, `taxonID` stays empty.

### Trait value and unit

The adapter resolves the value and the unit each on its own, then converts once.

1. The value is `traitValue` when stored, otherwise the parsed `verbatimTraitValue`.
2. The unit is `traitUnit` when stored, otherwise the printed `verbatimTraitUnit`.
3. The pair is converted with the unit table to grams for mass or millimetres for length. This changes nothing when the unit is already `g` or `mm`.

A stored `traitValue` is in the effective unit, which is `traitUnit` when stored and the printed unit otherwise. An extractor that changes the scale of a value must also store the unit.

A unit that is unknown or not valid for the trait drops the row. A value that is not a positive decimal drops the row.

### Citation and interpretation columns

- A free-text column that accepts a citation or a correction uses the interpretation value when the extractor stored a correction, and the printed citation otherwise. The narrow citation of a correction is not used for the column. It stays in the artifact for tracing and for UI highlighting, where a precise range is better than a whole passage.
- A column that accepts a citation or an interpretation (`individualCount`, `measurementValueMin`, dates, coordinates, and the like) is parsed or matched by the adapter. A citation is parsed from its text. An interpretation uses its `value`, and its passage is only the grounding. The adapter parses a number into the numeric ETS column. The artifact does not store the parsed number.
- A closed-vocabulary column (`sex`, `basisOfRecord`, `aggregateMeasure`) is matched against the ETS values. A value that does not match adds a warning and leaves the column empty.
- A date is written as `YYYY-MM-DD`. A year-first number or a written month is converted. A numeric day and month is converted when one number is greater than 12, so that number is the day. When both are 12 or less, the date is left empty.
- A minimum greater than the maximum drops the row.

### Defaults and assigned columns

- `basisOfRecord` is `literatureData` when the observation did not record one. `aggregateMeasure` stays empty.
- `measurementID` is assigned when the rows are built.
- `references` comes from the job's document.
- `warnings` collects what the adapter found while building the row.
- `occurrenceID` is copied when the observation stored one.
- `traitName` and `traitID` stay empty until the thesaurus exists.

## Warnings and drops

A warning means the row was built with something missing or doubtful. A drop means no row was built.

A dropped observation must not disappear silently. The adapter returns, with the rows, a list of drops. Each has the source index of the observation in the artifact and a reason. It also keeps each row's source index, so a row can be traced back to its observation. The index is not derived from `measurementID`, which counts only kept rows.

## Open decisions

- Which of `verbatimScientificName` and `scientificName` the taxonomy lookup tries first, and which one is copied into ETS `scientificName` before a taxonomy source exists.
- The taxonomy source.
- The trait thesaurus.
