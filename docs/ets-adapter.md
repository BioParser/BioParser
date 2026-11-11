# ETS adapter

BioParser targets the Ecological Trait-data Standard (ETS) v0.10. The vocabulary is published at <https://ecologicaltraitdata.github.io/ETS/>.

The ETS adapter turns an extraction artifact ([extraction-artifact.md](extraction-artifact.md)) into ETS rows. It is the quality gate for what a user is shown. The artifact stores what the paper said, and the adapter decides which of it is good enough to become a row.

## Role

- The adapter reads an artifact and returns rows. It does not modify the artifact and does not write ETS columns back into it.
- It does not call the model, and it does not open the parser artifact.
- One observation is one row. The adapter never drops an observation. What it cannot standardize stays as printed, with a warning.
- A new ETS version changes the adapter's mapping. It does not change the stored artifact.
- The result is one row per observation. Each row has its source index, the observation's position in the artifact. `measurementID` is that position plus one. The result also names the `trait_list_version` that was used.

## Name mapping

The artifact uses its own camelCase names. The adapter maps them to ETS column names in one place. Only these differ:

| Artifact | ETS v0.10 |
|---|---|
| `taxonClass` | `class` |
| `measurementValueMin` | `measurementValue_min` |
| `measurementValueMax` | `measurementValue_max` |

`traitCode` is not an ETS column. When the observation has one, the adapter uses it to look up `traitName`, `traitID`, and the standard unit. When it has none, those columns stay empty.

## Sources

The adapter needs two sources. One is not chosen yet.

- **Taxonomy backbone** for `scientificName` and `taxonID`. The source is chosen later.
- **Trait thesaurus** for `traitName`, `traitID`, the stored unit, and the allowed levels. ETS does not define the trait list, so the mammal trait list is the thesaurus. See [Trait thesaurus](#trait-thesaurus).

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

The adapter is what converts. An extractor does not store a converted pair, such as `12500` and `g` for a printed `12.5 kg`. The unit check is not written into the artifact.

What happens next depends on the observation's `traitCode`.

**A code the list accepts.** The entry's kind decides:

- **Numeric.** The value is a positive decimal and the unit is one the entry accepts. `traitValue` and `traitUnit` are the converted value and the stored unit.
- **Count.** The value is a positive whole number and is not converted. A count the paper prints as a mean or a median, such as a litter size of 3.5, may be a decimal. The observation says so with `centralKind` or `aggregateMeasure`. `traitUnit` is the count's own unit, such as `young` or `teats`. The printed unit is not checked, because papers print `unitless`, `-`, or `NA` there. It stays in `verbatimTraitUnit`. A real measurement unit other than the count's own, such as `g`, is evidence the code is wrong, so the row is not standardized and warns `unit_not_for_trait:traitUnit`.
- **Categorical.** The value must match one of the entry's levels, ignoring case. `traitUnit` is empty. A real measurement unit, such as `mm`, is not standardized and warns `unit_not_for_trait:traitUnit`. A level has no mean to disperse, so the row still gets the ETS defaults of `1` for `individualCount` and `0` for `dispersion`, and that `0` carries no meaning. A stored dispersion, minimum, maximum, central choice, or dispersion choice is warned about with `not_applicable` and not published. `dispersion` is `0`, the minimum and the maximum are empty, and `statisticalMethod` is empty.

**No standardized value.** A code is a claim that the measurement is one defined trait. The claim does not hold when the observation has no code, the code is not on the list, the unit is not known or not valid for that trait, a level is not on the entry's list, or the value is not a number the trait accepts. The row is kept anyway:

- `traitName`, `traitID`, and `traitUnit` are empty.
- `traitValue` is the printed value, unconverted. The printed unit stays in `verbatimTraitUnit`.
- The range and the dispersion are not converted either. See [Range and dispersion](#range-and-dispersion).
- The warning says why. See [Warnings](#warnings).

### Range and dispersion

The minimum, the maximum, and the dispersion share the trait's unit. ETS gives the minimum and the maximum no unit of their own, and it does not convert `dispersion`, so this is the adapter's rule. ETS has no verbatim column for these three. A `not_converted` warning is how a reader knows one is not in the trait's unit.

| Stored choice | ETS column | Conversion |
|---|---|---|
| `dispersionKind` `standardDeviation` or `standardError` | `dispersion` | Same factor as the trait value |
| `dispersionKind` `variance` | `dispersion` | Square of that factor |
| A stored minimum or maximum | `measurementValue_min`, `measurementValue_max` | Same factor as the trait value |

An empty side stays empty.

**When the number is not converted.** The number stays in the printed unit and warns `not_converted:<field>`:

- The row has no trait factor, because it has no code or the list refused it. All three numbers stay printed.
- A dispersion number has no usable kind, either because the choice is absent or because it is not recognized.

A trait factor of 1 means the number is already in grams or millimetres, so it is kept and does not warn.

**Reading rule.** A row with a `not_converted` warning for a number has that number in the printed unit. Read it with `verbatimTraitUnit`. A number with no such warning is in the stored unit.

**Statistical method.** `centralKind` has no column and no conversion. It only feeds `statisticalMethod`, which stays one free-text phrase.

- A stored phrase is copied.
- With no stored phrase, the adapter writes one from `centralKind` and, when a dispersion number was stored, from `dispersionKind`. `mean` and `standardDeviation` become `mean and standard deviation`. The minimum and the maximum are their own columns, so they are not part of the phrase.
- An unrecognized `centralKind` warns `not_in_vocabulary:statisticalMethod`. A stored phrase is still copied.

**Other cases.**

- A dispersion kind with no number warns `unspecified:dispersion`, and `dispersion` takes the default (see [Defaults](#defaults-and-assigned-columns)). The generated phrase does not name that dispersion.
- No dispersion number and no kind leaves `dispersion` at 0, with no warning. A single specimen stores none of these choices.
- A minimum greater than the maximum, after conversion, is not published. Both stay empty and the row warns `bounds_reversed:measurementValue_min,measurementValue_max`.

### Citation and interpretation columns

- **Free text.** A column that accepts a citation or a correction uses the interpretation value when the extractor stored a correction, and the printed citation otherwise. The narrow citation of a correction is not used for the column. It stays in the artifact for tracing and for UI highlighting, where a precise range is better than a whole passage.
- **Numbers and dates.** A column that accepts a citation or an interpretation (`individualCount`, `measurementValueMin`, dates, coordinates, and the like) is parsed or matched by the adapter. A citation is parsed from its text. An interpretation uses its `value`, and its passage is only the grounding. The artifact does not store the parsed number.
- **Closed values.** `sex` and `aggregateMeasure` are matched against the ETS values. A value that does not match warns and leaves the column empty. So does a `countryCode` that is not two uppercase letters. A `basisOfRecord` that does not match warns and becomes `literatureData`.
- **Dates.** A date is written as `YYYY-MM-DD`. A year-first number or a written month is converted. A numeric day and month is converted when one number is greater than 12, so that number is the day. When both are 12 or less, the date is left empty.

### Defaults and assigned columns

- `basisOfRecord` is `literatureData` when the observation did not record one. `aggregateMeasure` stays empty.
- A missing `individualCount` is `1`. A missing `dispersion` is `0`. ETS defines those defaults. The `0` means the measurement did not report a dispersion. It is not a measured dispersion of zero. The minimum and the maximum have no default, so they stay empty.
- `measurementID` is assigned when the rows are built.
- `references` comes from the job's document.
- `warnings` collects what the adapter found while building the row.
- `occurrenceID` is copied when the observation stored one.
- `traitName` and `traitID` come from the thesaurus entry for the artifact's `traitCode`. When the observation has no code, both stay empty and the row warns `unmapped:traitName`.

## Warnings

A warning means the row was built with something missing or doubtful. No observation is dropped. The `warnings` column is one string. Each warning is written `kind:field` and the warnings are joined with ` | `, such as `unmapped:traitName | not_converted:dispersion`. The `field` is the ETS column the warning is about.

### Trait mapping warnings

These describe how the trait list treated the measurement. The Standardized column says whether the row still has its `traitName`, `traitID`, and converted `traitValue`. When it is **no**, `traitName`, `traitID`, and `traitUnit` are empty, and `traitValue` is the printed value, unconverted.

| Warning | Standardized | Meaning |
|---|---|---|
| `unmapped:traitName` | no | The observation has no trait code, or the code is not on the list. |
| `not_in_vocabulary:traitValue` | no | A categorical value is not one of the trait's levels. |
| `not_numeric:traitValue` | no | The value is not a number the trait accepts, such as `3.5` for a count. |
| `unit_unknown:traitUnit` | no | The printed unit is not known for any numeric trait. |
| `unit_not_for_trait:traitUnit` | no | The printed unit is a real measurement unit that the trait does not accept, such as `kHz` for a gestation length, or `g` for a count or a categorical trait. A numeric value cannot be converted, and for a count or a level the unit is evidence the code is wrong. |
| `deprecated_trait:traitName` | yes | The code is deprecated. It still converts. |
| `approximate:traitValue` | yes | The value used a conventional factor, a month or a year. |

### Range and dispersion warnings

| Warning | Meaning |
|---|---|
| `not_converted:dispersion`, `not_converted:measurementValue_min`, `not_converted:measurementValue_max` | The number stayed in the printed unit. Read it with `verbatimTraitUnit`. See [Range and dispersion](#range-and-dispersion) for when this happens. |
| `unspecified:dispersion` | A dispersion kind was stored with no number. `dispersion` takes the default. |
| `bounds_reversed:measurementValue_min,measurementValue_max` | The minimum is greater than the maximum after conversion. Both stay empty. |
| `not_applicable:dispersion`, `not_applicable:measurementValue_min`, `not_applicable:measurementValue_max`, `not_applicable:centralKind`, `not_applicable:dispersionKind` | A categorical observation stored a spread, a range, or a mean. A level has none of these, so the trait may be wrong. The value is not published. |

### Other column warnings

| Warning | Meaning |
|---|---|
| `not_numeric:<field>` | The printed number is not the ETS numeric type, or is out of range. The column stays empty. The field is `individualCount`, `age`, `measurementValue_min`, `measurementValue_max`, `dispersion`, `year`, `month`, `day`, `decimalLongitude`, `decimalLatitude`, or `elevation`. |
| `not_in_vocabulary:<field>` | A stored token is outside the list for that column. The column stays empty, except `basisOfRecord`, which becomes `literatureData`, and `statisticalMethod`, where a stored phrase is still copied. The field is `sex`, `basisOfRecord`, `aggregateMeasure`, `countryCode`, or `statisticalMethod`. |
| `not_a_date:eventDate`, `not_a_date:measurementDeterminedDate` | The printed date is not a calendar day. The column stays empty. |
| `not_a_date:year,month,day` | Year, month, and day together are not one calendar day. The day is cleared. |

A taxon lookup that finds no hit or more than one will add a warning when a taxonomy source exists. None is run now.

## Trait thesaurus

The thesaurus is [`mammal_traits.json`](../src/bioparser/traits/mammal_traits.json). The models that validate it are in [`table.py`](../src/bioparser/traits/table.py), in the `bioparser.traits` package so the extractor and the API can read it without importing the adapter. `json_schema()` exports its JSON Schema. The list is a starting point for the mammal extractor. It is derived from PanTHERIA, COMBINE, and the traits most often reported in client papers.

Each entry has:

- a `code`, the internal key the model emits and an artifact stores. It is not an ETS column.
- a preferred `name`, which is `traitName`.
- a `trait_id`, which is `traitID`. It is a Vertebrate Trait Ontology term when one matches the definition, such as `VT:0001259` for body mass. Otherwise it is an opaque `BPT:` identifier published with this list. The prefix-to-IRI mapping is in the file. A `BPT:` identifier has no IRI yet, so it does not resolve to a web page.
- a one-sentence `definition` that says which measurement the code means. Head-body length and total length are separate codes, even though both are stored in millimetres.
- a `domain`: morphology, reproduction, life_history, life_habit, population, physiology, or acoustics. A domain is a label for selecting a subset. The adapter ignores it.
- a value `kind`:
  - **numeric**: the printed units it accepts, each with its factor into the stored unit. Mass is stored in grams, length in millimetres, and age in days. A month is 30.4375 days and a year is 365.25 days, so those conversions are approximate.
  - **count**: a number of things. The entry names what is counted, such as young or teats. It is not converted.
  - **categorical**: a closed set of levels and no unit.

Rules that keep stored results valid:

- A code keeps its meaning. A change to the measurement or the stored unit is a new code with a new identifier.
- A retired code is marked `deprecated`, with an optional `replaced_by`. It still converts. Only a code that is absent from the list is treated as no code.
- A name or an identifier may change. An artifact stores only the code, so the next run uses the new value.
- The list has a `version` in the form `YYYY.MM.REVISION`: the year and month of the release, then a revision that starts at 0 and counts further releases in that month. Every change to the file, including a new trait or a deprecation, bumps it. The adapter result reports it.
- `data/traits/releases/` holds a frozen copy of every released version, outside the installed package. After an edit, bump `version` and run `uv run python -m bioparser.traits.release`.
- Tests enforce these rules. They fail if the list changes without a new release. They also fail if a code is removed or changes kind, stored unit or a printed unit factor, loses a level or an approximate unit, or becomes active again after a deprecation.

The model is not asked to emit a `traitID`. The extractor keeps its closed set of trait codes.

## Open decisions

- Which of `verbatimScientificName` and `scientificName` the taxonomy lookup tries first.
- The taxonomy source.
