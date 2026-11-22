from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bioparser.hashing import Sha256Hex

PARSER_ARTIFACT_SCHEMA_VERSION = "2"
_EDGE_PLACES = 4


def _round_page_coord(value: float) -> float:
    return round(value, _EDGE_PLACES)


class BlockKind(StrEnum):
    TEXT = "text"
    TABLE = "table"


class BlockRole(StrEnum):
    TITLE = "title"
    HEADING = "heading"
    BODY = "body"
    LIST = "list"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    HEADER = "header"
    FOOTER = "footer"
    REFERENCE = "reference"


# hide_input_in_errors: a ValidationError must not quote its input (paper text or
# model output); pipeline failures log their traceback.
class BoundingBox(BaseModel):
    """Axis-aligned box in page space, top-left origin, normalized to [0, 1]."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _ordered_and_in_unit_square(self) -> "BoundingBox":
        for name, value in (
            ("x0", self.x0),
            ("y0", self.y0),
            ("x1", self.x1),
            ("y1", self.y1),
        ):
            if value < 0.0 or value > 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {value}")
        if self.x1 < self.x0:
            raise ValueError("x1 must be greater than or equal to x0")
        if self.y1 < self.y0:
            raise ValueError("y1 must be greater than or equal to y0")
        return self


class TextLine(BaseModel):
    """One horizontal line, with a horizontal edge for each stored character.

    ``x0[i]`` and ``x1[i]`` are the glyph edges of ``text[start + i]`` on the block
    that owns this line. A null pair is a stored character with no ink: a space
    inserted between words, a hyphen removed at a line join, or MinerU's ``^{}``
    and ``_{}`` markers. A glyph that covers several characters shares its box in equal slices.
    Coordinates are stored to four decimal places.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    start: int
    end: int
    y0: float
    y1: float
    x0: list[float | None]
    x1: list[float | None]

    @model_validator(mode="after")
    def _edges_match_the_range(self) -> "TextLine":
        if self.start < 0:
            raise ValueError("start must be >= 0")
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        width = self.end - self.start
        if len(self.x0) != width or len(self.x1) != width:
            raise ValueError("x0 and x1 must have one entry per character")
        if not 0.0 <= self.y0 <= 1.0 or not 0.0 <= self.y1 <= 1.0:
            raise ValueError("y0 and y1 must be in [0, 1]")
        if self.y1 < self.y0:
            raise ValueError("y1 must be greater than or equal to y0")
        for left, right in zip(self.x0, self.x1, strict=True):
            if left is None and right is None:
                continue
            if left is None or right is None:
                raise ValueError("a glyph edge must set both x0 and x1")
            if not 0.0 <= left <= 1.0 or not 0.0 <= right <= 1.0:
                raise ValueError("glyph edges must be in [0, 1]")
            if right < left:
                raise ValueError("x1 must be greater than or equal to x0")
        # Four decimal places. Full floats make the stored artifact much larger.
        self.y0 = _round_page_coord(self.y0)
        self.y1 = max(_round_page_coord(self.y1), self.y0)
        rounded_x0: list[float | None] = []
        rounded_x1: list[float | None] = []
        for left, right in zip(self.x0, self.x1, strict=True):
            if left is None or right is None:
                rounded_x0.append(left)
                rounded_x1.append(right)
                continue
            left = _round_page_coord(left)
            right = max(_round_page_coord(right), left)
            rounded_x0.append(left)
            rounded_x1.append(right)
        self.x0 = rounded_x0
        self.x1 = rounded_x1
        return self


def boxes_for_range(lines: list[TextLine], start: int, end: int) -> list[BoundingBox]:
    """One rectangle per line the half-open range touches, from the glyph edges inside it."""
    if end <= start:
        return []
    boxes: list[BoundingBox] = []
    for line in lines:
        lo = max(start, line.start)
        hi = min(end, line.end)
        if lo >= hi:
            continue
        offset = lo - line.start
        count = hi - lo
        edges = [
            (left, right)
            for left, right in zip(
                line.x0[offset : offset + count],
                line.x1[offset : offset + count],
                strict=True,
            )
            if left is not None and right is not None
        ]
        if not edges:
            continue
        boxes.append(
            BoundingBox(
                x0=min(left for left, _ in edges),
                y0=line.y0,
                x1=max(right for _, right in edges),
                y1=line.y1,
            )
        )
    return boxes


class TextContent(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    type: Literal["text"] = "text"
    text: str
    heading_level: int | None = None
    lines: list[TextLine] = Field(default_factory=list)

    @model_validator(mode="after")
    def _lines_fit_text(self) -> "TextContent":
        previous = 0
        for line in self.lines:
            if line.start < previous or line.end > len(self.text):
                raise ValueError("lines must sit inside the text without overlapping")
            previous = line.end
        return self


class TableCell(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    row: int
    column: int
    text: str
    rowspan: int = 1
    colspan: int = 1
    bbox: BoundingBox | None = None


class TableContent(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    type: Literal["table"] = "table"
    cells: list[TableCell]


BlockContent = Annotated[
    TextContent | TableContent,
    Field(discriminator="type"),
]


class ContentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    block_id: str
    order: int
    kind: BlockKind
    role: BlockRole
    content: BlockContent
    bbox: BoundingBox | None = None
    continuation_of: str | None = None


class Page(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    page_number: int
    width: float
    height: float
    blocks: list[ContentBlock]


class ParserInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str
    version: str
    configuration: dict[str, str | int | float | bool]


class RepairStatus(StrEnum):
    """Whether the symbol-font repair changed the PDF the parsers read."""

    SKIPPED = "skipped"
    SUCCESS = "success"
    FAILED = "failed"


class ProblemCode(StrEnum):
    """A gap the parse noticed. The parse still succeeded."""

    # The Advent 3B2 repair did not run, so the original PDF was read.
    ADVENT_FONTS_UNCHECKED = "advent_fonts_unchecked"
    # Drawn Advent symbols replaced with the Unicode replacement character.
    ADVENT_SYMBOLS_REPLACED = "advent_symbols_replaced"
    # Drawn characters in an Advent font that has not been verified. They were kept.
    ADVENT_CHARACTERS_UNVERIFIED = "advent_characters_unverified"
    # Pages whose Advent content stream could not be counted.
    ADVENT_PAGES_UNCOUNTED = "advent_pages_uncounted"


class ParseProblem(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    code: ProblemCode
    # How many items this code counts. Zero when the problem is that a check did not run.
    count: int = Field(default=0, ge=0)


class ParserArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    schema_version: str
    checksum: Sha256Hex
    parser: ParserInfo
    pages: list[Page]
    # Empty when the parse finished with nothing to warn about.
    problems: list[ParseProblem] = Field(default_factory=list)
