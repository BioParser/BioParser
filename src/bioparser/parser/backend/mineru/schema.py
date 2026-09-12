"""MinerU pipeline ``middle.json`` layout dump.

Written against the pinned CLI in ``MINERU_PIPELINE_VERSION``. One object with
``pdf_info``: pages in file order. Each page has ``page_idx``, ``page_size``
``[width, height]`` in PDF points, and ``para_blocks`` in reading order. A block
may nest further ``blocks``, or be a leaf with ``lines`` of ``spans``. Title
blocks use ``level`` (1-based); ``text_level`` belongs to MinerU's
``content_list.json``, not this dump.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Pipeline CLI this dump schema is written against. Bump together with
# ``MINERU_TOOL_SPEC``, the fixture, and the mapper if the dump shape changes.
MINERU_PIPELINE_VERSION = "3.4.5"
PIPELINE_BACKEND = "pipeline"
PARSE_METHOD = "auto"

BBox = tuple[float, float, float, float]
PageSize = tuple[float, float]


class MiddleSpan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    content: str | None = None
    html: str | None = None
    bbox: BBox
    cross_page: bool = False


class MiddleLine(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bbox: BBox
    spans: list[MiddleSpan] = Field(default_factory=list)


class MiddleBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    bbox: BBox
    level: int | None = None
    lines: list[MiddleLine] = Field(default_factory=list)
    blocks: list[MiddleBlock] = Field(default_factory=list)


class MiddlePage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page_idx: int
    page_size: PageSize
    para_blocks: list[MiddleBlock] = Field(default_factory=list)


class MiddleDump(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pdf_info: list[MiddlePage] = Field(min_length=1)
    backend: str = Field(alias="_backend")
    version_name: str = Field(alias="_version_name")


# Nested ``blocks`` is annotated as this same class; rebuild now that it exists.
MiddleBlock.model_rebuild()
