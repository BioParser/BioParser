# Ecological Trait-data Standard

BioParser targets ETS v0.10. The vocabulary is published at <https://ecologicaltraitdata.github.io/ETS/>.

The extraction artifact stores what the paper said. It checks the shape of an observation and does not set a quality bar. The ETS adapter is the later step that builds the standardized ETS columns from this file, and it is the quality gate for what a user is shown. It does not write those columns back into the artifact. An extractor may use the same checks to refine a result before storing it, and it may store a looser one.

## Stored observation

One observation is one measurement, which is one ETS row.

The artifact records the extractor name and the extractor version once. It does not record a model id or other extractor configuration. Each observation has:

- Trait: `trait`, one of `body_mass`, `body_length`, or `tail_length`. The artifact defines this set. A code outside it is rejected.
- Required fields: `verbatimScientificName`, `verbatimTraitValue`, and `verbatimTraitUnit`. Each has a non-empty string.
- Optional fields: `verbatimTraitName`, `taxonRank`, `kingdom`, `phylum`, `class`, `order`, `family`, `genus`, `sex`, `lifeStage`, `age`, `morphotype`, `basisOfRecord`, `measurementMethod`, `measurementResolution`, `measurementRemarks`, `aggregateMeasure`, `statisticalMethod`, `individualCount`, `dispersion`, `measurementValue_min`, `measurementValue_max`, and `measurementAccuracy`. Each is a cited string, including counts and numeric values such as `individualCount` and `measurementValue_min`. An observation that has no value for one stores `null`.

### Cited fields

A cited field stores the printed string. It also stores a range when that string was located in the paper:

```json
{"text": "12.5", "evidence": {"block_id": "b0", "start": 27, "end": 31}}
```

`text` is the ETS verbatim value. It is the narrow token, such as `12.5` or `g`, not the sentence around it. `evidence` is a half-open range `[start, end)` in the text of that block. `start` and `end` are Unicode code points, the same indexes as a Python `str`, not UTF-16 code units. `end` is greater than `start`.

When the range is present, `text` is exactly that slice. The schema does not open the parsed text. Before storing, the worker checks every range against the parsed text, whichever extractor produced it. The block must exist, and `text` must be exactly that slice. A mismatch fails the job and nothing is stored. A field with no range is the extractor's choice and is returned unchanged.

`evidence` may be `null`. The string is still stored. The observation stays, and its other fields can still have ranges. The ETS adapter decides what an ETS row does with a field that was not located. An extractor may apply its own rules before storing, and drop or repair a field so the stored result is better.

## How the current extractor locates a value

The default extractor does not ask the model for character offsets. The model names one block and quotes a sentence that contains the value. That sentence is only a locator, and it is not stored.

The extractor writes a range when the sentence occurs once in the block and the field string occurs once inside the sentence. It checks that `text` is exactly that slice. If either string is missing or repeated, it stores `text` and sets `evidence` to `null`.

Another extractor can find the same range another way. The artifact does not record how the range was found.

## What the ETS adapter derives

The ETS adapter needs three sources:

- A taxonomy backbone for `scientificName` and `taxonID`. The source is chosen later.
- A trait thesaurus for `traitName`, `traitID`, and the standard unit. ETS does not define the trait list, so the thesaurus is chosen later. The artifact's codes stay `body_mass`, `body_length`, and `tail_length` until then.
- The unit table already used by the extractor. It checks that the printed unit belongs to the trait. The ETS adapter uses the same table to convert mass to grams and length to millimetres. Neither the check nor the converted value is written into the artifact.

`verbatimScientificName` stays the printed span, including forms such as `M. musculus`. The ETS adapter sends that string to the taxonomy source. One confident hit becomes `scientificName` and `taxonID`. No hit, or more than one, leaves `taxonID` empty, copies the printed string into `scientificName`, and sets ETS `warnings`.

The ETS adapter parses cited strings such as `individualCount` and `measurementValue_min` into the numeric ETS columns. The artifact does not.

Until the taxonomy source exists, the ETS adapter copies `verbatimScientificName` and leaves `taxonID` empty. `basisOfRecord` is `literatureData` when the observation did not record one. `references` comes from the job's document. `measurementID` is assigned when the response is built. `occurrenceID` stays empty.

A later extractor may call these same helpers and give a failed match back to the model. The helper results still do not go into the extraction artifact.
