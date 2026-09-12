"""Map MinerU's middle.json dump into a ParserArtifact.

Mapped:

- text leaves → ``TextContent`` (equation spans included as text)
- table HTML → ``TableContent``

Left unparsed:

- ``image``, ``image_body``, ``chart``, ``chart_body``
- ``discarded_blocks`` (headers/footers MinerU already dropped)
- span types other than text, equations, and table HTML
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from bioparser.parser.backend.mineru.schema import (
    MINERU_PIPELINE_VERSION,
    MiddleBlock,
    MiddleDump,
)
from bioparser.parser.backend.mineru.tables import table_from_leaf
from bioparser.parser.backend.mineru.text import fragments_from_leaf
from bioparser.parser.checksum import block_id
from bioparser.parser.models import (
    PARSER_ARTIFACT_SCHEMA_VERSION,
    BlockKind,
    BlockRole,
    BoundingBox,
    ContentBlock,
    Page,
    ParserArtifact,
    ParserInfo,
    TableContent,
    TextContent,
)

SKIPPED_BLOCK_TYPES = frozenset(
    {
        "image_body",
        "chart_body",
        "image",
        "chart",
    }
)


@dataclass(frozen=True, slots=True)
class _PreparedBlock:
    page_offset: int
    kind: BlockKind
    role: BlockRole
    content: TextContent | TableContent
    bbox: BoundingBox | None


def _iter_leaves(block: MiddleBlock) -> list[MiddleBlock]:
    if block.blocks:
        leaves: list[MiddleBlock] = []
        for child in block.blocks:
            leaves.extend(_iter_leaves(child))
        return leaves
    return [block]


def _prepare_leaf(
    leaf: MiddleBlock,
    *,
    page_sizes: list[tuple[float, float]],
    page_index: int,
) -> list[_PreparedBlock]:
    if leaf.type in SKIPPED_BLOCK_TYPES:
        return []
    width, height = page_sizes[page_index]
    table = table_from_leaf(leaf, width=width, height=height)
    if table is not None:
        content, bbox = table
        return [
            _PreparedBlock(
                page_offset=0,
                kind=BlockKind.TABLE,
                role=BlockRole.BODY,
                content=content,
                bbox=bbox,
            )
        ]
    return [
        _PreparedBlock(
            page_offset=fragment.page_offset,
            kind=BlockKind.TEXT,
            role=fragment.role,
            content=fragment.content,
            bbox=fragment.bbox,
        )
        for fragment in fragments_from_leaf(
            leaf,
            page_sizes=page_sizes,
            page_index=page_index,
        )
    ]


def _append_prepared(
    blocks_by_page: list[list[ContentBlock]],
    *,
    checksum: str,
    page_numbers: list[int],
    source_index: int,
    prepared: _PreparedBlock,
    continuation_of: str | None,
) -> str:
    dest = min(max(source_index + prepared.page_offset, 0), len(blocks_by_page) - 1)
    order = len(blocks_by_page[dest])
    page_number = page_numbers[dest]
    ident = block_id(checksum, page_number, order)
    blocks_by_page[dest].append(
        ContentBlock(
            block_id=ident,
            order=order,
            kind=prepared.kind,
            role=prepared.role,
            content=prepared.content,
            bbox=prepared.bbox,
            continuation_of=continuation_of,
        )
    )
    return ident


def artifact_from_middle_json(
    middle: dict[str, Any],
    *,
    checksum: str,
    parser_name: str,
    parser_version: str,
    configuration: dict[str, str | int | float | bool],
) -> ParserArtifact:
    try:
        dump = MiddleDump.model_validate(middle)
    except ValidationError as exc:
        raise ValueError("middle.json is invalid") from exc

    if dump.version_name != MINERU_PIPELINE_VERSION:
        raise ValueError(
            f"middle.json came from MinerU {dump.version_name!r}; this mapper is "
            f"written against {MINERU_PIPELINE_VERSION!r}. Rebuild the mineru image, "
            f"or update schema.py, the mapper and the fixtures together."
        )

    page_sizes = [page.page_size for page in dump.pdf_info]
    page_numbers = [page.page_idx + 1 for page in dump.pdf_info]
    blocks_by_page: list[list[ContentBlock]] = [[] for _ in dump.pdf_info]
    extracted_any = False

    for page_index, page in enumerate(dump.pdf_info):
        for para in page.para_blocks:
            for leaf in _iter_leaves(para):
                prepared_blocks = _prepare_leaf(
                    leaf,
                    page_sizes=page_sizes,
                    page_index=page_index,
                )
                prev_id: str | None = None
                for prepared in prepared_blocks:
                    ident = _append_prepared(
                        blocks_by_page,
                        checksum=checksum,
                        page_numbers=page_numbers,
                        source_index=page_index,
                        prepared=prepared,
                        continuation_of=prev_id,
                    )
                    extracted_any = True
                    prev_id = ident

    if not extracted_any:
        raise ValueError("middle.json contained no mappable text blocks")

    pages = [
        Page(page_number=number, width=width, height=height, blocks=blocks)
        for number, (width, height), blocks in zip(
            page_numbers, page_sizes, blocks_by_page, strict=True
        )
    ]
    return ParserArtifact(
        schema_version=PARSER_ARTIFACT_SCHEMA_VERSION,
        checksum=checksum,
        parser=ParserInfo(name=parser_name, version=parser_version, configuration=configuration),
        pages=pages,
    )
