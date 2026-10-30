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

`traitCode` is not an ETS column. The adapter uses it to look up `traitName`, `traitID`, and the standard unit.

## Sources

The adapter needs two sources. One is not chosen yet.

- **Taxonomy backbone** for `scientificName` and `taxonID`. The source is chosen later.
- **Trait thesaurus** for `traitName`, `traitID`, the stored unit, and the allowed levels. ETS does not define the trait list, so the mammal trait list is the thesaurus. See [Trait thesaurus](#trait-thesaurus).

An extractor may store `traitValue` and `traitUnit` when the printed token is not itself the value, such as `kg` for a printed `kilograms`. Both are what the paper printed. The adapter converts that pair. The unit check is not written into the artifact.

## How a row is derived

### Name and taxon

`verbatimScientificName` stays the printed span, including forms such as `M. musculus`. `scientificName`, when stored, is another name a taxonomy lookup can try.

One confident hit becomes the ETS `scientificName` and `taxonID`. No hit, or more than one, leaves `taxonID` empty and adds a warning. The observation is not rewritten.

Until a taxonomy source exists, ETS `scientificName` is the stored `scientificName` when the observation has one, and otherwise the verbatim citation. `taxonID` stays empty. No warning is added for the missing lookup, because none is run.

### Trait value and unit

The adapter resolves the value and the unit each on its own, then converts once. Both are what the paper printed.

1. The value is `traitValue` when stored, otherwise the parsed `verbatimTraitValue`. A stored value is a cleaned reading of the printed number, in the printed unit.
2. The unit is `traitUnit` when stored, otherwise the printed `verbatimTraitUnit`. A stored unit is a cleaned reading of the printed unit, such as `kg` for `kilograms`.
3. The pair is converted with the trait's entry in the thesaurus to the trait's stored unit. This changes nothing when the printed unit is already the stored unit.

The adapter is what converts. An extractor does not store a converted pair, such as `12500` and `g` for a printed `12.5 kg`.

A trait that is not on the list drops the row with `trait_unknown`. For a numeric trait, an unknown unit drops the row with `unit_unknown`, and a unit that is not valid for that trait drops it with `unit_not_for_trait`. A value that is not a positive decimal drops the row with `value_not_numeric`. A count is a positive whole number and is not converted. A count the paper prints as a mean or a median, such as a litter size of 3.5, may be a decimal. The observation says so with `centralKind` or `aggregateMeasure`. A categorical trait must match one of its levels, ignoring case, or the row drops with `level_unknown`. The printed unit of a count or a categorical trait is not checked, because papers print `unitless`, `-`, or `NA` there. It stays in `verbatimTraitUnit`, and `traitUnit` is the count's own unit, `individuals` for most counts, and empty for a categorical trait. A categorical row still gets the ETS defaults of `1` for `individualCount` and `0` for `dispersion`. For a categorical trait the `0` carries no meaning, because there is no mean to disperse.

### Range and dispersion

The minimum, the maximum, and the dispersion share the trait's unit. ETS gives the minimum and the maximum no unit of their own, and it does not convert `dispersion`. A stored minimum or maximum is converted with the same factor as the trait value. An empty side stays empty.

| Stored choice | ETS column | Conversion |
|---|---|---|
| `dispersionKind` `standardDeviation` or `standardError` | `dispersion` | Same factor as the trait value |
| `dispersionKind` `variance` | `dispersion` | Square of that factor |
| A stored minimum or maximum | `measurementValue_min`, `measurementValue_max` | Same factor as the trait value |

`centralKind` has no column and no conversion. It only feeds the `statisticalMethod` phrase.

`statisticalMethod` stays one free-text phrase. A stored phrase is copied. When no phrase is stored, the adapter writes one from `centralKind` and, when a dispersion number was stored, from `dispersionKind`. The minimum and the maximum are their own columns, so they are not part of the phrase. `mean` and `standardDeviation` become `mean and standard deviation`.

- An unrecognized `centralKind` warns `not_in_vocabulary:statisticalMethod`. A stored phrase is still copied.
- A dispersion number with no usable kind stays in the printed unit and warns `not_converted:dispersion`. An absent choice and an unrecognized choice both do this. A factor of 1 means the number is already in grams or millimetres, so it is kept and there is no warning.
- A dispersion kind with no number warns `unspecified:dispersion` and `dispersion` takes the default (see [Defaults](#defaults-and-assigned-columns)). The generated phrase does not name that dispersion.
- No dispersion number and no kind leaves `dispersion` at 0, with no warning. A single specimen stores none of these choices.
- A minimum greater than the maximum, after conversion, drops the row with `bounds_reversed`.

### Citation and interpretation columns

- A free-text column that accepts a citation or a correction uses the interpretation value when the extractor stored a correction, and the printed citation otherwise. The narrow citation of a correction is not used for the column. It stays in the artifact for tracing and for UI highlighting, where a precise range is better than a whole passage.
- A column that accepts a citation or an interpretation (`individualCount`, `measurementValueMin`, dates, coordinates, and the like) is parsed or matched by the adapter. A citation is parsed from its text. An interpretation uses its `value`, and its passage is only the grounding. The adapter parses a number into the numeric ETS column. The artifact does not store the parsed number.
- `sex` and `aggregateMeasure` are matched against the ETS values. A value that does not match adds a warning and leaves the column empty. A `countryCode` that is not two uppercase letters does the same. A `basisOfRecord` that does not match adds a warning and becomes `literatureData`.
- A date is written as `YYYY-MM-DD`. A year-first number or a written month is converted. A numeric day and month is converted when one number is greater than 12, so that number is the day. When both are 12 or less, the date is left empty.

### Defaults and assigned columns

- `basisOfRecord` is `literatureData` when the observation did not record one. `aggregateMeasure` stays empty.
- A missing `individualCount` is `1`. A missing `dispersion` is `0`. ETS defines those defaults. The `0` means the measurement did not report a dispersion. It is not a measured dispersion of zero. The minimum and the maximum have no default, so they stay empty.
- `measurementID` is assigned when the rows are built.
- `references` comes from the job's document.
- `warnings` collects what the adapter found while building the row.
- `occurrenceID` is copied when the observation stored one.
- `traitName` and `traitID` come from the thesaurus entry for the artifact's `traitCode`.

## Warnings and drops

A warning means the row was built with something missing or doubtful. A drop means no row was built.

Trait warnings:

- `not_applicable:dispersion`, `not_applicable:measurementValue_min`, and `not_applicable:measurementValue_max`: a categorical observation stores a spread or a range. A level has neither, so the trait may be wrong.
- `unit_not_expected:traitUnit`: a count or a categorical trait was printed with a real unit such as `g` or `mm`. The trait may be wrong.
- `deprecated_trait:traitName`: the code is deprecated. It still converts.
- `approximate:traitValue`: the value used a conventional factor, a month or a year.

A dropped observation must not disappear silently. The adapter returns, with the rows, a list of drops. Each has the source index of the observation in the artifact and a reason: `trait_unknown`, `unit_unknown`, `unit_not_for_trait`, `value_not_numeric`, `level_unknown`, or `bounds_reversed`. The result also names the `trait_list_version` that was used. It also keeps each row's source index, so a row can be traced back to its observation. The index is not derived from `measurementID`, which counts only kept rows.

## Trait thesaurus

The thesaurus is [`mammal_traits.json`](../src/bioparser/ets/mammal_traits.json). The models that validate it are in [`traits.py`](../src/bioparser/ets/traits.py), and `json_schema()` exports its JSON Schema. The list is a starting point for the mammal extractor. It is derived from PanTHERIA, COMBINE, and the traits most often reported in client papers.

Each entry has:

- a `code`, the internal key the model emits and an artifact stores. It is not an ETS column.
- a preferred `name`, which is `traitName`.
- a `trait_id`, which is `traitID`. It is a Vertebrate Trait Ontology term when one matches the definition, such as `VT:0001259` for body mass. Otherwise it is an opaque `BPT:` identifier published with this list. The prefix-to-IRI mapping is in the file. A `BPT:` identifier has no IRI yet, so it does not resolve to a web page.
- a one-sentence `definition` that says which measurement the code means. Head-body length and total length are separate codes, even though both are stored in millimetres.
- a `domain`: morphology, reproduction, life_habit, home_range, density, physiology, or acoustics. A domain is a label for selecting a subset. The adapter ignores it.
- a value `kind`:
  - **numeric**: the printed units it accepts, each with its factor into the stored unit. Mass is stored in grams, length in millimetres, and age in days. A month is 30.4375 days and a year is 365.25 days, so those conversions are approximate.
  - **count**: a number of things, with a unit of individuals unless the entry names its own, such as teats. It is not converted.
  - **categorical**: a closed set of levels and no unit.

Rules that keep stored results valid:

- A code keeps its meaning. A change to the measurement or the stored unit is a new code with a new identifier.
- A retired code is marked `deprecated`, with an optional `replaced_by`. It still converts. Only a code that is absent from the list is `trait_unknown`.
- A name or an identifier may change. An artifact stores only the code, so the next run uses the new value.
- The list has a `version` in the form `YYYY.MM.REVISION`: the year and month of the release, then a revision that starts at 0 and counts further releases in that month. Every change to the file, including a new trait or a deprecation, bumps it. The adapter result reports it.

The model is not asked to emit a `traitID`. The extractor keeps its closed set of trait codes.

## Open decisions

- Which of `verbatimScientificName` and `scientificName` the taxonomy lookup tries first.
- The taxonomy source.
