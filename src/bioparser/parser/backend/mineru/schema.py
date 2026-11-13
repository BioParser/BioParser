"""MinerU 4 ``middle.json``: a DocVortex ``docvortex.middle`` document, protocol 2.0.

Written against the pinned service in ``MINERU_VERSION``. ``pages`` are in file order, each
with a zero-based ``page_idx`` and ``blocks`` in reading order. A block's ``bbox`` is already
normalized to the page ([0, 1], top-left origin); there are no line or span boxes.
``content`` is a string (equation LaTeX, table HTML), a list of inline spans (text-like
blocks), or, for the types in ``CONTAINER_TYPES``, a list of child blocks. Page sizes in PDF
points are in ``extensions.docvortex_layout``.
"""

from __future__ import annotations

import itertools
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bioparser.services.mineru.client import OCR_MODE, MinerUTier

# Service this schema is written against. Bump together with the mineru image pin, the
# fixture, and the mapper if the document shape changes.
MINERU_VERSION = "4.0.12"

# Blocks whose content is child blocks, not text.
CONTAINER_TYPES = frozenset({"list", "index", "image", "table", "chart", "code"})


def parse_configuration(tier: MinerUTier) -> dict[str, str | int | float | bool]:
    return {"tier": tier, "ocr_mode": OCR_MODE}


BBox = tuple[float, float, float, float]

# hide_input_in_errors: a ValidationError must not quote its input (paper text or
# model output); pipeline failures log their traceback.
_CONFIG = ConfigDict(extra="ignore", hide_input_in_errors=True)


class MiddleSpan(BaseModel):
    """``text``, ``equation_inline`` or ``code_inline``, or a ``hyperlink`` of such spans."""

    model_config = _CONFIG

    type: str
    content: str | list[MiddleSpan] = ""
    # Of a text span: bold, italic, superscript, subscript and so on.
    styles: list[str] = Field(default_factory=list)


class MiddleBlock(BaseModel):
    model_config = _CONFIG

    type: str
    bbox: BBox | None = None
    level: int | None = None
    continues_prev: bool = False
    content: str | list[MiddleSpan] = ""
    # A container's children, moved here from ``content`` so a leaf is a block without them.
    blocks: list[MiddleBlock] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _children_to_blocks(cls, data: Any) -> Any:
        if (
            isinstance(data, dict)
            and isinstance(kind := data.get("type"), str)
            and kind in CONTAINER_TYPES
        ):
            return {**data, "content": "", "blocks": data.get("content", [])}
        return data


class MiddlePage(BaseModel):
    model_config = _CONFIG

    page_idx: int = Field(ge=0)
    blocks: list[MiddleBlock] = Field(default_factory=list)


class PageGeometry(BaseModel):
    model_config = _CONFIG

    page_idx: int = Field(ge=0)
    # Infinity is valid JSON to json.loads but is stored as null and could not be read back.
    width_pt: float = Field(gt=0, allow_inf_nan=False)
    height_pt: float = Field(gt=0, allow_inf_nan=False)


class LayoutExtension(BaseModel):
    model_config = _CONFIG

    version: Literal[1]
    pages: list[PageGeometry]


class MiddleExtensions(BaseModel):
    model_config = _CONFIG

    docvortex_layout: LayoutExtension


class Producer(BaseModel):
    model_config = _CONFIG

    name: str
    version: str


class MiddleMetadata(BaseModel):
    model_config = _CONFIG

    producer: Producer


class MiddleDump(BaseModel):
    model_config = _CONFIG

    schema_id: Literal["docvortex.middle"] = Field(alias="schema")
    schema_version: Literal["2.0"]
    metadata: MiddleMetadata
    extensions: MiddleExtensions
    pages: list[MiddlePage] = Field(min_length=1)

    @model_validator(mode="after")
    def _pages_in_file_order(self) -> MiddleDump:
        # Page numbers name blocks, so a repeated page would repeat block IDs.
        indexes = [page.page_idx for page in self.pages]
        if any(later <= earlier for earlier, later in itertools.pairwise(indexes)):
            raise ValueError("page_idx values must be unique and increasing")
        return self


# Both models nest themselves; rebuild now that the classes exist.
MiddleSpan.model_rebuild()
MiddleBlock.model_rebuild()
