from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bioparser.ets_vocab import AggregateMeasure, BasisOfRecord, Sex

Trait = Literal["body_mass", "body_length", "tail_length"]
CanonicalTraitUnit = Literal["g", "mm"]

EXTRACTION_ARTIFACT_SCHEMA_VERSION: Literal["1"] = "1"

_CONFIG = ConfigDict(extra="forbid", hide_input_in_errors=True)


class TextRange(BaseModel):
    """Half-open character range in one parser block.

    ``start`` and ``end`` are Unicode code points, the same indexes as a Python
    ``str``. ``end`` is greater than ``start``.
    """

    model_config = _CONFIG

    block_id: str = Field(min_length=1)
    start: int
    end: int

    @model_validator(mode="after")
    def _ordered(self) -> "TextRange":
        if self.start < 0 or self.end <= self.start:
            raise ValueError("evidence range is empty or out of order")
        return self


class Citation(BaseModel):
    """Printed text. On a citation-only column, this text is the ETS value.

    A range list, when present, concatenates to ``text``.
    """

    model_config = _CONFIG

    kind: Literal["citation"] = "citation"
    text: str = Field(min_length=1)
    evidence: list[TextRange] | None = Field(default=None, min_length=1)


class Interpretation(BaseModel):
    """A value read from one citation. ``passage`` is that citation."""

    model_config = _CONFIG

    kind: Literal["interpretation"] = "interpretation"
    value: str = Field(min_length=1)
    passage: Citation


class TraitInterpretation(BaseModel):
    """The trait code. It is not printed, and it is not an ETS column."""

    model_config = _CONFIG

    kind: Literal["interpretation"] = "interpretation"
    value: Trait
    passage: Citation


class SexInterpretation(BaseModel):
    """Interpreted sex. The value is an ETS sex token."""

    model_config = _CONFIG

    kind: Literal["interpretation"] = "interpretation"
    value: Sex
    passage: Citation


class BasisInterpretation(BaseModel):
    """Interpreted basisOfRecord. The value is an ETS basis token."""

    model_config = _CONFIG

    kind: Literal["interpretation"] = "interpretation"
    value: BasisOfRecord
    passage: Citation


class AggregateInterpretation(BaseModel):
    """Interpreted aggregateMeasure. The value is TRUE or FALSE."""

    model_config = _CONFIG

    kind: Literal["interpretation"] = "interpretation"
    value: AggregateMeasure
    passage: Citation


class TraitUnitInterpretation(BaseModel):
    """Interpreted traitUnit. The value is grams or millimetres."""

    model_config = _CONFIG

    kind: Literal["interpretation"] = "interpretation"
    value: CanonicalTraitUnit
    passage: Citation


class Correction(BaseModel):
    """A narrow citation kept beside an interpretation.

    ``citation`` is the narrow printed token. The interpretation's passage is
    the wider text its value was read from. Both the citation and the
    interpretation value are usable.
    """

    model_config = _CONFIG

    kind: Literal["correction"] = "correction"
    citation: Citation
    interpretation: Interpretation


# A column with no verbatim sibling. Its check can reject the printed text, so
# the field is a citation or an interpretation. A citation that already passes
# is kept. An interpretation supplies a value that can pass. The passage fails
# the check, so it is not a usable column value. The sex alias closes that value.
CitationOrInterpretation = Annotated[Citation | Interpretation, Field(discriminator="kind")]
CitationOrSexInterpretation = Annotated[Citation | SexInterpretation, Field(discriminator="kind")]
# A citation, or a correction that keeps the narrow token beside an
# interpretation of a wider passage. Both are usable.
CorrectableCitation = Annotated[Citation | Correction, Field(discriminator="kind")]


class Observation(BaseModel):
    """One measurement.

    A citation-only field is printed text. A correctable citation is that citation, or a
    correction that keeps the narrow citation beside an interpretation. A citation
    or interpretation has no verbatim sibling. Its check can reject the printed
    text, so it is a citation or an interpretation of one passage. The passage
    fails that check, so it is not a usable column value. ``basisOfRecord`` and
    ``aggregateMeasure`` are interpretations only. A paper does not print those
    tokens.

    ``scientificName``, ``traitValue``, and ``traitUnit`` are interpretations
    beside a required verbatim citation. The interpretation is omitted when the
    printed token is already the value. ``scientificName`` is another name a
    later taxonomy lookup can try. ``traitUnit`` is ``g`` or ``mm``.

    Every other ETS column a paper can state is optional. An extractor leaves it null.
    """

    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    trait: TraitInterpretation
    verbatimTraitName: CorrectableCitation | None = None

    scientificName: Interpretation | None = None
    verbatimScientificName: Citation

    traitValue: Interpretation | None = None
    verbatimTraitValue: Citation

    traitUnit: TraitUnitInterpretation | None = None
    verbatimTraitUnit: Citation

    taxonRank: CorrectableCitation | None = None
    kingdom: CorrectableCitation | None = None
    phylum: CorrectableCitation | None = None
    taxonClass: CorrectableCitation | None = Field(default=None, alias="class")
    order: CorrectableCitation | None = None
    family: CorrectableCitation | None = None
    genus: CorrectableCitation | None = None

    sex: CitationOrSexInterpretation | None = None
    lifeStage: CorrectableCitation | None = None
    age: CitationOrInterpretation | None = None
    morphotype: CorrectableCitation | None = None

    basisOfRecord: BasisInterpretation | None = None
    basisOfRecordDescription: CorrectableCitation | None = None

    measurementMethod: CorrectableCitation | None = None
    measurementResolution: CorrectableCitation | None = None
    measurementRemarks: CorrectableCitation | None = None
    measurementDeterminedBy: CorrectableCitation | None = None
    measurementDeterminedDate: CitationOrInterpretation | None = None

    aggregateMeasure: AggregateInterpretation | None = None
    statisticalMethod: CorrectableCitation | None = None
    individualCount: CitationOrInterpretation | None = None
    dispersion: CitationOrInterpretation | None = None
    measurementValue_min: CitationOrInterpretation | None = None
    measurementValue_max: CitationOrInterpretation | None = None
    measurementAccuracy: CorrectableCitation | None = None

    occurrenceID: CorrectableCitation | None = None

    eventID: CorrectableCitation | None = None
    preparations: CorrectableCitation | None = None
    samplingProtocol: CorrectableCitation | None = None
    year: CitationOrInterpretation | None = None
    month: CitationOrInterpretation | None = None
    day: CitationOrInterpretation | None = None
    eventDate: CitationOrInterpretation | None = None
    locationID: CorrectableCitation | None = None
    habitat: CorrectableCitation | None = None
    decimalLongitude: CitationOrInterpretation | None = None
    decimalLatitude: CitationOrInterpretation | None = None
    elevation: CitationOrInterpretation | None = None
    geodeticDatum: CorrectableCitation | None = None
    verbatimLocality: CorrectableCitation | None = None
    country: CorrectableCitation | None = None
    countryCode: CitationOrInterpretation | None = None
    occurrenceRemarks: CorrectableCitation | None = None

    @model_validator(mode="after")
    def _paired_field_has_its_verbatim_citation(self) -> "Observation":
        for paired, verbatim in (
            ("scientificName", "verbatimScientificName"),
            ("traitValue", "verbatimTraitValue"),
            ("traitUnit", "verbatimTraitUnit"),
        ):
            if getattr(self, paired) is not None and getattr(self, verbatim) is None:
                raise ValueError(f"{verbatim} is required when {paired} is set")
        return self


class ExtractionArtifact(BaseModel):
    """Versioned extraction result stored for a job."""

    model_config = _CONFIG

    schema_version: Literal["1"] = EXTRACTION_ARTIFACT_SCHEMA_VERSION
    extractor: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    observations: list[Observation] = Field(default_factory=list)
