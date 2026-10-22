# Ecological Trait-data Standard

BioParser targets ETS v0.10. The vocabulary is published at <https://ecologicaltraitdata.github.io/ETS/>.

The extraction artifact stores what the paper said. It checks the shape of an observation and does not set a quality bar. The ETS adapter is the later step that builds the standardized ETS columns from this file, and it is the quality gate for what a user is shown. It does not write those columns back into the artifact. An extractor may use the same checks to refine a result before storing it.

Every text in the artifact is grounded. A citation always has a range in the parsed text, so any field can be traced to its source. An extractor that cannot ground a field does not store it. If the field is required, the extractor drops the observation. What the model attempted and could not ground belongs in the extractor's logs, not in the artifact. An unresolved name or a unit that cannot be converted is still stored, because it is grounded. The user-facing response is where a row is rewritten or dropped.

## Names and values

Field names are the artifact's own, in camelCase, and follow the ETS terms where there is one. `class` is `taxonClass`, and `measurementValue_min` is `measurementValueMin`. The adapter maps these to ETS column names for the ETS version it targets, so a new ETS version changes the adapter and not the stored artifact.

The artifact does not enforce a set of allowed values. An interpretation `value` is a non-empty string. That includes `trait`, `sex`, `basisOfRecord`, `aggregateMeasure`, and `traitUnit`. An extractor may constrain its model to a set, or check afterwards. The adapter decides what a value means and warns on one it does not know.

## Stored observation

One observation is one measurement, which is one ETS row.

The artifact records the extractor name and the extractor version once. Each observation has:

- Trait: `trait`, an interpretation. The value is any non-empty string, such as `body_mass`. The artifact does not enforce a list of traits. The passage is the citation it is read from. The code is not an ETS column.
- Citation only. The required verbatim columns are the printed token: `verbatimScientificName`, `verbatimTraitValue`, and `verbatimTraitUnit`. An interpretation on one of these is rejected.
- Citation or correction: `verbatimTraitName`, `taxonRank`, `kingdom`, `phylum`, `taxonClass`, `order`, `family`, `genus`, `lifeStage`, `morphotype`, `basisOfRecordDescription`, `measurementMethod`, `measurementResolution`, `measurementRemarks`, `measurementDeterminedBy`, `measurementAccuracy`, `statisticalMethod`, `occurrenceID`, `eventID`, `preparations`, `samplingProtocol`, `locationID`, `habitat`, `geodeticDatum`, `verbatimLocality`, `country`, and `occurrenceRemarks`. A citation is the narrow printed token. A correction keeps that token beside an interpretation whose passage is the wider text the value was read from. The narrow citation and the interpretation value are both usable.
- Paired with a verbatim column: `scientificName` with `verbatimScientificName`, `traitValue` with `verbatimTraitValue`, and `traitUnit` with `verbatimTraitUnit`. The verbatim citation is required. The paired field is an interpretation, omitted when the printed token is already the value. `scientificName` is a name read from the printed citation, such as `Mus musculus` from `M. musculus`. Which of the two a later step uses first is not decided yet.
- Citation or interpretation, because the column has no verbatim sibling and its check can reject the printed text: `sex`, `age`, `measurementDeterminedDate`, `dispersion`, `individualCount`, `measurementValueMin`, `measurementValueMax`, `year`, `month`, `day`, `eventDate`, `decimalLongitude`, `decimalLatitude`, `elevation`, and `countryCode`. The check is a token list, a number, a date, or an ISO 3166-1 alpha-2 code. A printed token that already passes is a citation. A value read from wider text is an interpretation: `8 males and 4 females` read as `12`, `8 Mar 1963` read as `1963-03-08`, `Germany` read as `DE`. The passage fails the check, so it is not a usable column value. It is not a correctable citation, because that passage is not a fallback. An observation that has no value for one stores `null`.
- Interpretation only: `basisOfRecord` and `aggregateMeasure`. A paper does not print `LivingSpecimen` or `TRUE`. An observation that has no value for one stores `null`.

### Citation

A citation stores the printed string and the ranges where it was located:

```json
{"kind": "citation", "text": "12.5", "evidence": [{"block_id": "b0", "start": 27, "end": 31}]}
```

`text` is printed text. On its own it is the narrow token, such as `12.5` or `g`. As the passage of an interpretation it is the wider text the value was read from. Each item of `evidence` is a half-open range `[start, end)` in one parser block. `start` and `end` are Unicode code points, the same indexes as a Python `str`, not UTF-16 code units. `end` is greater than `start`. One range is the usual case. A second range is a paragraph that continues in the next column. The ranges follow the parser's block order, and `text` is the concatenation of those slices.

`evidence` is required and has at least one range. An interpretation stores a `value` read from one passage, and the passage is a citation. The value is not the slice. A correction stores the narrow citation and that interpretation together. The passage is wider than the citation. Both the narrow citation and the interpretation value are usable.

The schema does not open the parsed text. Before storing, the worker checks every range against the parsed text, whichever extractor produced it. Each block must exist, the ranges must be in reading order, and `text` must be the joined slices. A mismatch fails the job and nothing is stored. The interpretation value is not looked up in the passage. An extractor drops what it cannot ground before it returns the artifact.

## How the current extractor locates a value

The default extractor does not ask the model for character offsets. The model names one block and quotes a sentence that contains the value. That sentence is only a locator, and it is not stored.

The extractor writes a range when the sentence occurs once in the block and the field string occurs once inside the sentence. It checks that `text` is exactly that slice. If either string is missing or repeated for any field of a measurement, the extractor drops that measurement. The trait code is stored as an interpretation whose passage is that same sentence.

Another extractor can find the same range another way. The artifact does not record how the range was found.

## What the ETS adapter derives

The ETS adapter needs three sources:

- A taxonomy backbone for `scientificName` and `taxonID`. The source is chosen later.
- A trait thesaurus for `traitName`, `traitID`, and the standard unit. ETS does not define the trait list, so the thesaurus is chosen later. The artifact does not check the trait against a list. The adapter checks it against the thesaurus. `traitName` and `traitID` come from the same entry and stay off the observation.
- The unit table already used by the extractor. It checks that the printed unit belongs to the trait. The ETS adapter uses the same table to convert mass to grams and length to millimetres when `traitValue` and `traitUnit` were left unset. An extractor may store that converted pair itself. The check is not written into the artifact.

`verbatimScientificName` stays the printed span, including forms such as `M. musculus`. `scientificName`, when stored, is another name a taxonomy lookup can try. Which of the two is tried first is not decided yet. One confident hit becomes the ETS `scientificName` and `taxonID`. No hit, or more than one, leaves `taxonID` empty and sets ETS `warnings`. The observation is not rewritten.

The ETS adapter parses a citation such as `individualCount` or `measurementValueMin` into the numeric ETS column. An interpretation already holds the value read from its passage. The artifact does not store the parsed number.

Until the taxonomy source exists, `taxonID` stays empty. Which stored name is copied into ETS `scientificName` is not decided yet. `basisOfRecord` is `literatureData` when the observation did not record one. `measurementID`, `warnings`, and `references` are assigned when the row is built. `references` comes from the job's document. `occurrenceID` is copied when the observation stored one.

A later extractor may call these same helpers and give a failed match back to the model, for iterative improvement.
