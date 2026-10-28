"""Map MinerU's middle.json dump into a ParserArtifact.

Mapped:

- text leaves → ``TextContent`` (inline and display equations included as LaTeX text)
- ``table_body`` HTML → ``TableContent``
- ``continues_prev`` → ``continuation_of`` the previous leaf of the same type, as DocVortex
  links them: a paragraph continues the last paragraph or text list item, past figures, tables
  and code; a table continues the previous table through its body

Left unparsed:

- ``image_body``, ``chart_body``
- ``header``, ``footer``, ``page_number``, ``aside_text`` (3.x put these in
  ``discarded_blocks``, which was never mapped)
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from bioparser.parser.backend.mineru.schema import MINERU_VERSION, MiddleBlock, MiddleDump
from bioparser.parser.backend.mineru.tables import table_from_html
from bioparser.parser.backend.mineru.text import bbox_from_unit, role_and_heading, text_from_leaf
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
        "header",
        "footer",
        "page_number",
        "aside_text",
    }
)
LIST_TYPES = frozenset({"list", "index"})


class MiddleJsonContractError(ValueError):
    """middle.json is not the MinerU release and schema this mapper is written against."""


@dataclass(frozen=True, slots=True)
class _PreparedBlock:
    leaf_type: str
    kind: BlockKind
    role: BlockRole
    content: TextContent | TableContent
    bbox: BoundingBox | None


def _iter_leaves(
    block: MiddleBlock, *, in_list: bool = False
) -> Iterator[tuple[MiddleBlock, bool]]:
    """Leaves in reading order, each with whether it sits in a list or an index."""
    in_list = in_list or block.type in LIST_TYPES
    if not block.blocks:
        yield block, in_list
        return
    for child in block.blocks:
        yield from _iter_leaves(child, in_list=in_list)


def _prepare_leaf(leaf: MiddleBlock, *, in_list: bool) -> _PreparedBlock | None:
    if leaf.type in SKIPPED_BLOCK_TYPES:
        return None
    bbox = bbox_from_unit(leaf.bbox)
    if leaf.type == "table_body":
        table = table_from_html(leaf.content) if isinstance(leaf.content, str) else None
        if table is None:
            return None
        return _PreparedBlock(
            leaf_type=leaf.type, kind=BlockKind.TABLE, role=BlockRole.BODY, content=table, bbox=bbox
        )
    text = text_from_leaf(leaf)
    if not text.strip():
        return None
    role, heading_level = role_and_heading(leaf, in_list=in_list)
    return _PreparedBlock(
        leaf_type=leaf.type,
        kind=BlockKind.TEXT,
        role=role,
        content=TextContent(text=text, heading_level=heading_level),
        bbox=bbox,
    )


def _page_sizes(dump: MiddleDump) -> list[tuple[float, float]]:
    geometry = {
        page.page_idx: (page.width_pt, page.height_pt)
        for page in dump.extensions.docvortex_layout.pages
    }
    try:
        return [geometry[page.page_idx] for page in dump.pages]
    except KeyError as exc:
        raise MiddleJsonContractError(
            f"middle.json has no size for page index {exc.args[0]}"
        ) from None


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
        raise MiddleJsonContractError("middle.json is invalid") from exc

    produced_by = dump.metadata.producer.version
    if produced_by != MINERU_VERSION:
        raise MiddleJsonContractError(
            f"middle.json came from MinerU {produced_by!r}; this mapper is "
            f"written against {MINERU_VERSION!r}. Rebuild the mineru image, "
            f"or update schema.py, the mapper and the fixtures together."
        )

    pages: list[Page] = []
    # The block a later ``continues_prev`` block continues, by leaf type.
    last_of_type: dict[str, str] = {}
    for page, (width, height) in zip(dump.pages, _page_sizes(dump), strict=True):
        page_number = page.page_idx + 1
        blocks: list[ContentBlock] = []
        for top in page.blocks:
            continues = top.continues_prev
            # A continued table links its body; its caption reads "(Continued)".
            carrier = "table_body" if top.type == "table" else None
            for leaf, in_list in _iter_leaves(top):
                prepared = _prepare_leaf(leaf, in_list=in_list)
                if prepared is None:
                    continue
                links = continues and carrier in (None, prepared.leaf_type)
                ident = block_id(checksum, page_number, len(blocks))
                blocks.append(
                    ContentBlock(
                        block_id=ident,
                        order=len(blocks),
                        kind=prepared.kind,
                        role=prepared.role,
                        content=prepared.content,
                        bbox=prepared.bbox,
                        continuation_of=last_of_type.get(prepared.leaf_type) if links else None,
                    )
                )
                last_of_type[prepared.leaf_type] = ident
                continues = continues and not links
        pages.append(Page(page_number=page_number, width=width, height=height, blocks=blocks))

    if not any(page.blocks for page in pages):
        raise ValueError("middle.json contained no mappable text blocks")

    return ParserArtifact(
        schema_version=PARSER_ARTIFACT_SCHEMA_VERSION,
        checksum=checksum,
        parser=ParserInfo(name=parser_name, version=parser_version, configuration=configuration),
        pages=pages,
    )
