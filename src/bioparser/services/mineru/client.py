from __future__ import annotations

import json
import logging
from typing import Any

import httpx2

# Invocation knobs only. Provenance is not this module's job: the caller that
# builds the artifact reads pipeline_configuration() from schema directly.
from bioparser.logging_config import stopwatch
from bioparser.parser.backend.mineru.schema import PARSE_METHOD, PIPELINE_BACKEND

# A 20 MiB PDF can yield a middle_json far larger than itself
# Refuse the body rather than discover the ceiling as an OOMKill.
# TODO: We should probably make these bigger?
# TODO: implement in config?
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

logger = logging.getLogger(__name__)


class MinerUError(RuntimeError):
    """mineru-api rejected the document, or was unreachable."""


class MinerUClient:
    def __init__(self, base_url: str, timeout: float, connect_timeout: float) -> None:
        self._http = httpx2.AsyncClient(
            base_url=base_url,
            timeout=httpx2.Timeout(timeout, connect=connect_timeout),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _read_capped(self, filename: str, content: bytes) -> bytes:
        """Stream the response, reject if response > MAX_RESPONSE_BYTES.

        Streamed so that an body > MAX_RESPONSE_BYTES is abandoned early, instead of
        being fully read and only then rejected

        Logs one record per request: status_code (None if
        no response arrived), bytes_read (decoded body bytes) and latency_ms.
        Only sizes are logged.
        """
        chunks: list[bytes] = []
        total = 0
        status_code: int | None = None
        error: str | None = None
        elapsed_ms = stopwatch()
        try:
            async with self._http.stream(
                "POST",
                "/file_parse",
                files={"files": (filename, content, "application/pdf")},
                data={
                    "backend": PIPELINE_BACKEND,
                    "parse_method": PARSE_METHOD,
                    "return_md": "false",
                    "return_middle_json": "true",
                    "return_content_list": "false",
                    "return_images": "false",
                },
            ) as response:
                status_code = response.status_code
                if response.status_code != 200:
                    raise MinerUError(f"mineru-api returned {response.status_code}")
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise MinerUError(
                            f"mineru-api response exceeded {MAX_RESPONSE_BYTES} bytes"
                        )
                    chunks.append(chunk)
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            fields: dict[str, object] = {
                "status_code": status_code,
                "bytes_read": total,
                "latency_ms": elapsed_ms(),
            }
            if error is None:
                logger.info("mineru-api request completed", extra=fields)
            else:
                logger.warning("mineru-api request failed", extra=fields | {"error": error})
        return b"".join(chunks)

    async def parse(self, *, filename: str, content: bytes) -> dict[str, Any]:
        """POST /file_parse and return the parsed middle.json object."""

        try:
            body_bytes = await self._read_capped(filename, content)
        except httpx2.HTTPError as exc:
            raise MinerUError(f"mineru-api unreachable: {type(exc).__name__}") from exc

        try:
            body = json.loads(body_bytes)
        except ValueError as exc:
            raise MinerUError("mineru-api returned a non-JSON body") from exc

        return self._middle_json_from_body(body)

    @staticmethod
    def _middle_json_from_body(body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise MinerUError("mineru-api returned a non-object body")

        results = body.get("results")

        if not isinstance(results, dict) or not results:
            raise MinerUError("mineru-api returned no usable results object")

        payload = next(iter(results.values()))
        if not isinstance(payload, dict):
            raise MinerUError("mineru-api returned a non-object result entry")

        middle_json = payload.get("middle_json")
        if not middle_json or not isinstance(middle_json, str):
            raise MinerUError("mineru-api returned no middle_json")

        try:
            parsed = json.loads(middle_json)
        except json.JSONDecodeError as exc:
            raise MinerUError("mineru-api did not return a proper json") from exc

        if not isinstance(parsed, dict):
            raise MinerUError("mineru-api returned a non-object middle-json")
        return parsed
