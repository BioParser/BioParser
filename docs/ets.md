# Ecological Trait-data Standard

BioParser targets ETS v0.10. The vocabulary is published at <https://ecologicaltraitdata.github.io/ETS/>.

The extraction artifact stores what the paper said. It checks the shape of an observation and does not set a quality bar. The ETS adapter is the later step that builds the standardized ETS columns from this file, and it is the quality gate for what a user is shown. It does not write those columns back into the artifact. An extractor may use the same checks to refine a result before storing it, and it may store a looser one.

Keeping the looser result makes the pipeline easier to develop. A missing range, an unresolved name, or a unit that cannot be converted is still in the file, so the model's attempt is still there to read. A debug route or a script can show that file, which is more than the user is shown. The user-facing response is where a row is rewritten or dropped.

## Stored observation

One observation is one measurement, which is one ETS row.

The artifact records the extractor name and the extractor version once. Each observation has:

- Trait: `trait`, an interpretation. The value is one of `body_mass`, `body_length`, or `tail_length`. The passage is the citation it is read from. A code outside the list is rejected. The code is not an ETS column.
- Citation only. The required verbatim columns are the printed token: `verbatimScientificName`, `verbatimTraitValue`, and `verbatimTraitUnit`. An interpretation on one of these is rejected.
- Citation or correction: `verbatimTraitName`, `taxonRank`, `kingdom`, `phylum`, `class`, `order`, `family`, `genus`, `lifeStage`, `morphotype`, `measurementMethod`, `measurementResolution`, `measurementRemarks`, `measurementAccuracy`, and `statisticalMethod`. A citation is the narrow printed token. A correction keeps that token beside an interpretation whose passage is the wider text the value was read from. The narrow citation and the interpretation value are both usable.
- Paired with a verbatim column: `scientificName` with `verbatimScientificName`, `traitValue` with `verbatimTraitValue`, and `traitUnit` with `verbatimTraitUnit`. The verbatim citation is required. The paired field is an interpretation, omitted when the printed token is already the value. `scientificName` is a name read from the printed citation, such as `Mus musculus` from `M. musculus`. `traitUnit` is `g` or `mm`. Which of the two a later step uses first is not decided yet.
- Citation or interpretation, because the column has no verbatim sibling and its check can reject the printed text: `sex`, `age`, `dispersion`, `individualCount`, `measurementValue_min`, and `measurementValue_max`. The check is a token list or a number. A printed token that already passes is a citation. A value read from wider text is an interpretation: `8 males and 4 females` read as `12`. The passage fails the check, so it is not a usable column value. It is not a correctable citation, because that passage is not a fallback. `sex` is `male`, `female`, `subadult`, `unknown`, or `hermaphrodite`. An observation that has no value for one stores `null`.
- Interpretation only: `basisOfRecord` and `aggregateMeasure`. A paper does not print `LivingSpecimen` or `TRUE`. `basisOfRecord` is `LivingSpecimen`, `PreservedSpecimen`, `FossilSpecimen`, `literatureData`, `traitDatabase`, or `expertKnowledge`. `aggregateMeasure` is `TRUE` or `FALSE`. An observation that has no value for one stores `null`.

### Citation

A citation stores the printed string. It also stores ranges when that string was located:

```json
{"kind": "citation", "text": "12.5", "evidence": [{"block_id": "b0", "start": 27, "end": 31}]}
```

`text` is printed text. On its own it is the narrow token, such as `12.5` or `g`. As the passage of an interpretation it is the wider text the value was read from. Each item of `evidence` is a half-open range `[start, end)` in one parser block. `start` and `end` are Unicode code points, the same indexes as a Python `str`, not UTF-16 code units. `end` is greater than `start`. One range is the usual case. A second range is a paragraph that continues in the next column. The ranges follow the parser's block order, and `text` is the concatenation of those slices.

`evidence` may be `null`. The string is still stored. An interpretation stores a `value` read from one passage, and the passage is a citation. The value is not the slice. A correction stores the narrow citation and that interpretation together. The passage is wider than the citation. Both the narrow citation and the interpretation value are usable.

The schema does not open the parsed text. Before storing, the worker checks every range against the parsed text, whichever extractor produced it. Each block must exist, the ranges must be in reading order, and `text` must be the joined slices. A mismatch fails the job and nothing is stored. A missing range list is left as the extractor returned it. The interpretation value is not looked up in the passage. The ETS adapter decides what an ETS row does with a field that was not located. An extractor may apply its own rules before storing, and drop or repair a field so the stored result is better.

## How the current extractor locates a value

The default extractor does not ask the model for character offsets. The model names one block and quotes a sentence that contains the value. That sentence is only a locator, and it is not stored.

The extractor writes a range when the sentence occurs once in the block and the field string occurs once inside the sentence. It checks that `text` is exactly that slice. If either string is missing or repeated, it stores `text` and sets `evidence` to `null`. The trait code is stored as an interpretation whose passage is that same sentence.

Another extractor can find the same range another way. The artifact does not record how the range was found.

## What the ETS adapter derives

The ETS adapter needs three sources:

- A taxonomy backbone for `scientificName` and `taxonID`. The source is chosen later.
- A trait thesaurus for `traitName`, `traitID`, and the standard unit. ETS does not define the trait list, so the thesaurus is chosen later. Until then, the `Trait` literal is that list: `body_mass`, `body_length`, and `tail_length`. When the source exists, the validator reads it and the literal goes away. `traitName` and `traitID` come from the same entry and stay off the observation.
- The unit table already used by the extractor. It checks that the printed unit belongs to the trait. The ETS adapter uses the same table to convert mass to grams and length to millimetres when `traitValue` and `traitUnit` were left unset. An extractor may store that converted pair itself. The check is not written into the artifact.

`verbatimScientificName` stays the printed span, including forms such as `M. musculus`. `scientificName`, when stored, is another name a taxonomy lookup can try. Which of the two is tried first is not decided yet. One confident hit becomes the ETS `scientificName` and `taxonID`. No hit, or more than one, leaves `taxonID` empty and sets ETS `warnings`. The observation is not rewritten.

The ETS adapter parses a citation such as `individualCount` or `measurementValue_min` into the numeric ETS column. An interpretation already holds the value read from its passage. The artifact does not store the parsed number.

Until the taxonomy source exists, `taxonID` stays empty. Which stored name is copied into ETS `scientificName` is not decided yet. `basisOfRecord` is `literatureData` when the observation did not record one. `references` comes from the job's document. `measurementID` is assigned when the response is built. `occurrenceID` stays empty.

A later extractor may call these same helpers and give a failed match back to the model, for iterative improvement.
