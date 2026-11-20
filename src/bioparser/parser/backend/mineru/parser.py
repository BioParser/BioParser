from __future__ import annotations

import asyncio
from pathlib import Path

from bioparser.hashing import sha256_bytes
from bioparser.parser.advent_repair.symbol_child import repair_pdf_in_child
from bioparser.parser.backend.mineru.mapper import (
    MiddleJsonContractError,
    artifact_from_middle_json,
)
from bioparser.parser.backend.mineru.schema import parse_configuration
from bioparser.parser.errors import (
    ParserBackendUnavailableError,
    ParserProcessError,
    ParserTimeoutError,
    UnsupportedDocumentError,
)
from bioparser.parser.models import ParserArtifact
from bioparser.parser_names import MINERU_PARSER_NAME, PARSER_VERSIONS
from bioparser.services.mineru.client import (
    DEFAULT_TIER,
    MAX_WAIT_SECONDS,
    MinerUClient,
    MinerUError,
    MinerUInputError,
    MinerUPermanentError,
    MinerUTier,
    MinerUTimeoutError,
)

_CONNECT_TIMEOUT_S = 5.0


class MinerUParser:
    """Sends the PDF to mineru-api, then maps its middle.json into a parser artifact."""

    def __init__(
        self, base_url: str, timeout_s: float | None = None, *, tier: MinerUTier = DEFAULT_TIER
    ) -> None:
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._tier: MinerUTier = tier

    async def parse(self, path: Path) -> ParserArtifact:
        pdf_path = path.expanduser().resolve()
        if not pdf_path.is_file():
            raise FileNotFoundError("PDF not found")
        content = await asyncio.to_thread(pdf_path.read_bytes)
        checksum = await asyncio.to_thread(sha256_bytes, content)
        repaired = await repair_pdf_in_child(pdf_path, original=content)
        wait_s = MAX_WAIT_SECONDS if self._timeout_s is None else self._timeout_s

        try:
            client = MinerUClient(
                self._base_url, wait_s, connect_timeout=_CONNECT_TIMEOUT_S, tier=self._tier
            )
            try:
                middle = await client.parse(filename=f"{checksum}.pdf", content=repaired.data)
            finally:
                await client.aclose()
        except MinerUTimeoutError as exc:
            raise ParserTimeoutError(f"MinerU ran longer than {wait_s:.0f} s. {exc}") from exc
        except MinerUInputError as exc:
            # mineru-api judged the PDF: corrupted, encrypted, or over its limits.
            raise UnsupportedDocumentError(str(exc)) from exc
        except MinerUPermanentError as exc:
            raise ParserBackendUnavailableError(str(exc)) from exc
        except MinerUError as exc:
            # Unreachable, busy, or failed: retried. parse_failed is a crash or a lack of
            # memory as well as a PDF mineru-api could not read.
            raise ParserProcessError(str(exc)) from exc

        try:
            return artifact_from_middle_json(
                middle,
                checksum=checksum,
                parser_name=MINERU_PARSER_NAME,
                parser_version=PARSER_VERSIONS[MINERU_PARSER_NAME],
                configuration=parse_configuration(self._tier),
                repair_status=repaired.status,
                unrepaired_count=repaired.count,
            )
        except MiddleJsonContractError as exc:
            # The service and this mapper disagree (a MinerU upgrade on one side): not the PDF.
            raise ParserBackendUnavailableError(str(exc)) from exc
        except ValueError as exc:
            raise UnsupportedDocumentError(str(exc)) from exc
