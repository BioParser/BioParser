from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PARSER_ARTIFACT_SCHEMA_VERSION = "1"


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


class BoundingBox(BaseModel):
    """Axis-aligned box in page space, top-left origin, normalized to [0, 1]."""

    model_config = ConfigDict(extra="forbid")

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


class TextSpan(BaseModel):
    """Word box mapped onto `TextContent.text` as a half-open character range."""

    model_config = ConfigDict(extra="forbid")

    start: int
    end: int
    line: int
    bbox: BoundingBox

    @model_validator(mode="after")
    def _ordered_range(self) -> "TextSpan":
        if self.start < 0:
            raise ValueError("start must be >= 0")
        if self.end < self.start:
            raise ValueError("end must be greater than or equal to start")
        if self.line < 0:
            raise ValueError("line must be >= 0")
        return self


class TextContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = "text"
    text: str
    heading_level: int | None = None
    spans: list[TextSpan] = Field(default_factory=list)


class TableCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row: int
    column: int
    text: str
    rowspan: int = 1
    colspan: int = 1
    bbox: BoundingBox | None = None


class TableContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["table"] = "table"
    cells: list[TableCell]


BlockContent = Annotated[
    TextContent | TableContent,
    Field(discriminator="type"),
]


class ContentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    order: int
    kind: BlockKind
    role: BlockRole
    content: BlockContent
    bbox: BoundingBox | None = None
    continuation_of: str | None = None


class Page(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int
    width: float
    height: float
    blocks: list[ContentBlock]


class ParserInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    configuration: dict[str, str | int | float | bool]


class ParserArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    checksum: str
    parser: ParserInfo
    pages: list[Page]
