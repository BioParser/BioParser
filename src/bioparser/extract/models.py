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
CitationOrCorrection = Annotated[Citation | Correction, Field(discriminator="kind")]


class Observation(BaseModel):
    """One measurement.

    A citation-only field is printed text. A citation or correction is that citation, or a
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
    verbatimTraitName: CitationOrCorrection | None = None

    scientificName: Interpretation | None = None
    verbatimScientificName: Citation

    traitValue: Interpretation | None = None
    verbatimTraitValue: Citation

    traitUnit: Interpretation | None = None
    verbatimTraitUnit: Citation

    taxonRank: CitationOrCorrection | None = None
    kingdom: CitationOrCorrection | None = None
    phylum: CitationOrCorrection | None = None
    taxonClass: CitationOrCorrection | None = None
    order: CitationOrCorrection | None = None
    family: CitationOrCorrection | None = None
    genus: CitationOrCorrection | None = None

    sex: CitationOrInterpretation | None = None
    lifeStage: CitationOrCorrection | None = None
    age: CitationOrInterpretation | None = None
    morphotype: CitationOrCorrection | None = None

    basisOfRecord: Interpretation | None = None
    basisOfRecordDescription: CitationOrCorrection | None = None

    measurementMethod: CitationOrCorrection | None = None
    measurementResolution: CitationOrCorrection | None = None
    measurementRemarks: CitationOrCorrection | None = None
    measurementDeterminedBy: CitationOrCorrection | None = None
    measurementDeterminedDate: CitationOrInterpretation | None = None

    aggregateMeasure: Interpretation | None = None
    statisticalMethod: CitationOrCorrection | None = None
    individualCount: CitationOrInterpretation | None = None
    dispersion: CitationOrInterpretation | None = None
    measurementValueMin: CitationOrInterpretation | None = None
    measurementValueMax: CitationOrInterpretation | None = None
    measurementAccuracy: CitationOrCorrection | None = None

    occurrenceID: CitationOrCorrection | None = None

    eventID: CitationOrCorrection | None = None
    preparations: CitationOrCorrection | None = None
    samplingProtocol: CitationOrCorrection | None = None
    year: CitationOrInterpretation | None = None
    month: CitationOrInterpretation | None = None
    day: CitationOrInterpretation | None = None
    eventDate: CitationOrInterpretation | None = None
    locationID: CitationOrCorrection | None = None
    habitat: CitationOrCorrection | None = None
    decimalLongitude: CitationOrInterpretation | None = None
    decimalLatitude: CitationOrInterpretation | None = None
    elevation: CitationOrInterpretation | None = None
    geodeticDatum: CitationOrCorrection | None = None
    verbatimLocality: CitationOrCorrection | None = None
    country: CitationOrCorrection | None = None
    countryCode: CitationOrInterpretation | None = None
    occurrenceRemarks: CitationOrCorrection | None = None


class ExtractionArtifact(BaseModel):
    """Versioned extraction result stored for a job."""

    model_config = _CONFIG

    schema_version: Literal["1"] = EXTRACTION_ARTIFACT_SCHEMA_VERSION
    extractor: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    observations: list[Observation] = Field(default_factory=list)
