from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    evidence: list[TextRange] = Field(min_length=1)


class Interpretation(BaseModel):
    """A value read from one citation. ``passage`` is that citation."""

    model_config = _CONFIG

    kind: Literal["interpretation"] = "interpretation"
    value: str = Field(min_length=1)
    passage: Citation


class Correction(BaseModel):
    """A narrow citation kept beside an interpretation.

    ``citation`` is the narrow printed token. The interpretation's passage is
    the wider text its value was read from. The adapter uses the
    interpretation value. The citation stays for tracing and highlighting.
    """

    model_config = _CONFIG

    kind: Literal["correction"] = "correction"
    citation: Citation
    interpretation: Interpretation


# A column with no verbatim sibling. Its check can reject the printed text, so
# the field is a citation or an interpretation. A citation that already passes
# is kept. An interpretation supplies a value that can pass. The passage fails
# the check, so it is not a usable column value.
CitationOrInterpretation = Annotated[Citation | Interpretation, Field(discriminator="kind")]
# A citation, or a correction that keeps the narrow token beside an
# interpretation of a wider passage. The interpretation is the column value.
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
    later taxonomy lookup can try.

    The artifact does not enforce a closed set of values. An interpretation's
    ``value`` is any non-empty string, including ``trait``. An extractor may
    constrain it, and the adapter decides what a value means.

    Every other column a paper can state is optional. An extractor leaves it null.
    """

    model_config = _CONFIG

    trait: Interpretation
    verbatimTraitName: CorrectableCitation | None = None

    scientificName: Interpretation | None = None
    verbatimScientificName: Citation

    traitValue: Interpretation | None = None
    verbatimTraitValue: Citation

    traitUnit: Interpretation | None = None
    verbatimTraitUnit: Citation

    taxonRank: CorrectableCitation | None = None
    kingdom: CorrectableCitation | None = None
    phylum: CorrectableCitation | None = None
    taxonClass: CorrectableCitation | None = None
    order: CorrectableCitation | None = None
    family: CorrectableCitation | None = None
    genus: CorrectableCitation | None = None

    sex: CitationOrInterpretation | None = None
    lifeStage: CorrectableCitation | None = None
    age: CitationOrInterpretation | None = None
    morphotype: CorrectableCitation | None = None

    basisOfRecord: Interpretation | None = None
    basisOfRecordDescription: CorrectableCitation | None = None

    measurementMethod: CorrectableCitation | None = None
    measurementResolution: CorrectableCitation | None = None
    measurementRemarks: CorrectableCitation | None = None
    measurementDeterminedBy: CorrectableCitation | None = None
    measurementDeterminedDate: CitationOrInterpretation | None = None

    aggregateMeasure: Interpretation | None = None
    statisticalMethod: CorrectableCitation | None = None
    individualCount: CitationOrInterpretation | None = None
    dispersion: CitationOrInterpretation | None = None
    measurementValueMin: CitationOrInterpretation | None = None
    measurementValueMax: CitationOrInterpretation | None = None
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


class ExtractionArtifact(BaseModel):
    """Versioned extraction result stored for a job."""

    model_config = _CONFIG

    schema_version: Literal["1"] = EXTRACTION_ARTIFACT_SCHEMA_VERSION
    extractor: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    observations: list[Observation] = Field(default_factory=list)
