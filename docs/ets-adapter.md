# ETS adapter

BioParser targets the Ecological Trait-data Standard (ETS) v0.10. The vocabulary is published at <https://ecologicaltraitdata.github.io/ETS/>.

The ETS adapter turns an extraction artifact ([extraction-artifact.md](extraction-artifact.md)) into ETS rows. It is the quality gate for what a user is shown. The artifact stores what the paper said, and the adapter decides which of it is good enough to become a row.

## Role

- The adapter reads an artifact and returns rows. It does not modify the artifact and does not write ETS columns back into it.
- It does not call the model, and it does not open the parser artifact.
- Each observation becomes one ETS row.
- A new ETS version changes the adapter's mapping. It does not change the stored artifact.

## Deviations from ETS

- A missing `individualCount` is empty. ETS says "Defaults to 1".
- A missing `dispersion` is empty. ETS says "Defaults to 0". A measured dispersion of zero keeps the spelling the paper printed.

## Name mapping

The artifact uses its own camelCase names. The adapter maps them to ETS column names in one place. Only these differ:

| Artifact | ETS v0.10 |
|---|---|
| `taxonClass` | `class` |
| `measurementValueMin` | `measurementValue_min` |
| `measurementValueMax` | `measurementValue_max` |

`traitCode` is not an ETS column. It is the extractor's code for the measurement, and a later trait list uses it to fill `traitName`, `traitID`, `traitValue`, and `traitUnit`. Until that list exists, those four stay empty.

## Sources

The adapter needs two sources. Neither is chosen yet.

- **Taxonomy backbone** for `scientificName` and `taxonID`. The source is chosen later.
- **Trait list** for `traitName`, `traitID`, `traitValue`, and `traitUnit`. ETS does not define the trait list, so it is chosen later. The artifact does not check `traitCode` against a list.

An extractor may store `traitValue` and `traitUnit` as its own reading of the printed number and unit, such as `kg` for a printed `kilograms`. The trait list uses that reading, and falls back to the printed text when there is none. It converts the pair into the trait's unit and writes the ETS columns.

## How a row is derived

### Name and taxon

`verbatimScientificName` stays the printed span, including forms such as `M. musculus`.

A stored `scientificName` is the model's reading of the accepted name, such as `Mus musculus` for a printed `M. musculus`. The row copies it. `taxonID` stays empty, and the row warns `not_verified:scientificName`, because the name was not checked against a taxonomy. An empty reading leaves the column empty and does not warn.

One confident taxonomy hit replaces that name and fills `taxonID` with it. The warning is then absent. No hit, or more than one, keeps the model's name, leaves `taxonID` empty, and keeps the warning. The observation is not rewritten. There is no lookup yet, so every stored name warns.

### Trait value and unit

The printed value and the printed unit are copied as they are.

1. `verbatimTraitValue` is the printed number. A paper that prints only a range leaves it empty, and the minimum and the maximum stay.
2. `verbatimTraitUnit` is the printed unit, such as `kg`. It is optional, and a measurement printed without a unit leaves it empty.
3. `traitValue` and `traitUnit` stay empty. A later trait list fills them when it standardizes the measurement.

### Range and dispersion

The minimum, the maximum, and the dispersion are the numbers the paper printed, read with `verbatimTraitUnit`. ETS gives them no unit of their own. An empty one stays empty.

A minimum greater than the maximum is kept as printed, and the row warns `bounds_reversed:measurementValue_min,measurementValue_max`.

`statisticalMethod` is the phrase the paper printed, or empty.

### Citation and interpretation columns

- A free-text column that accepts a citation or a correction uses the interpretation value when the extractor stored a correction, and the printed citation otherwise. The narrow citation of a correction is not used for the column. It stays in the artifact for tracing and for UI highlighting, where a precise range is better than a whole passage.
- A column that accepts a citation or an interpretation (`individualCount`, `measurementValueMin`, dates, coordinates, and the like) is parsed or matched by the adapter. A citation is parsed from its text. An interpretation uses its `value`, and its passage is only the grounding. A printed number keeps the spelling the paper printed, so `0.20` stays `0.20` and `03` stays `03`. Text that does not parse stays as printed and warns `not_numeric` for that field. The artifact does not store the parsed number.
- `sex` tries the reading first, then the printed word. The first one in the list is the column, in its canonical form. If neither is in the list, the printed word stays and the row warns `not_in_vocabulary:sex`.
- `countryCode` is two letters. Matching ignores case, and a match is written in uppercase, so `de` becomes `DE`. Other text stays and warns `not_in_vocabulary:countryCode`. The printed country name stays on `country`.
- `basisOfRecord` and `aggregateMeasure` are not printed tokens. A value outside those lists stays empty and warns `not_in_vocabulary` for that field.
- A date that parses is written as `YYYY-MM-DD`. A year-first number or a written month is converted. A numeric day and month is converted when one number is greater than 12, so that number is the day. When both are 12 or less, the printed text stays and warns `not_a_date` for that field. Year, month, and day that do not form a calendar day stay as printed and warn `not_a_date:year,month,day`. A number outside its column's range, such as month 13, a count below 1, or a longitude past 180, stays and warns `out_of_range` for that field.

### Defaults and assigned columns

- A missing `basisOfRecord` stays empty. ETS does not default it. `aggregateMeasure` stays empty.
- A missing `individualCount` is empty. A missing `dispersion` is empty. A measured dispersion of zero keeps the spelling the paper printed. The minimum and the maximum have no default either.
- `measurementID` is assigned when the rows are built.
- `references` comes from the job's document. The API writes the document checksum, the last segment of `pdf_ref`, until a bibliographic citation exists.
- `warnings` collects what the adapter found while building the row.
- `occurrenceID` is the printed token, or a value read from a wider phrase, when the observation stored one.
- `taxonRank` is inferred from `verbatimScientificName` in lowercase. Comparisons ignore case and leading or trailing whitespace; the stored name is unchanged. Names containing `sp.`, `spp.`, `cf.`, or `aff.` as separate words, or parentheses, leave the rank empty. Names with two or more words also leave it empty when the final word ends in a period. For one-word names, suffixes take precedence: `oidea` → `superfamily`, `idae` → `family`, `inae` → `subfamily`, `ini` → `tribe`, and `ina` → `subtribe`; otherwise the rank is `genus`. Two or three words give `species` or `subspecies`. Other word counts leave the rank empty. The observation is not rewritten. A future confident taxonomy hit replaces the inferred rank.
- A printed kingdom, phylum, class, order, family, or genus is copied, and a later lookup fills one of those columns only when the observation left it empty.
- `traitName`, `traitID`, `traitValue`, and `traitUnit` stay empty until the trait list exists.

## Warnings

A warning means the row was built with something missing or doubtful.

Each row keeps the source index of its observation in the artifact, so a row can be traced back.

## Open decisions

- Which of `verbatimScientificName` and `scientificName` the taxonomy lookup tries first.
- The taxonomy source.
- The trait list.
