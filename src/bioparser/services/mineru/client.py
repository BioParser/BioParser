from __future__ import annotations

import json
from typing import Any

import httpx2

from bioparser.parser.backend.mineru.schema import PARSE_METHOD, PIPELINE_BACKEND


def http_configuration() -> dict[str, str | int | float | bool]:
    return {"backend": PIPELINE_BACKEND, "parse_method": PARSE_METHOD}


# A 20 MiB PDF can yield a middle_json far larger than itself
# Refuse the body rather than discover the ceiling as an OOMKill.
# TODO: We should probably make these bigger?
# TODO: implement in config?
# TODO: add logger
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


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
        """
        chunks: list[bytes] = []
        total = 0
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
            if response.status_code != 200:
                raise MinerUError(f"mineru-api returned {response.status_code}")
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise MinerUError(f"mineru-api response exceeded {MAX_RESPONSE_BYTES} bytes")
                chunks.append(chunk)
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

        results = body.get("results") or {}

        if not results:
            raise MinerUError("mineru-api returned no results")

        payload = next(iter(results.values()))
        middle_json = payload.get("middle_json")
        if not middle_json:
            raise MinerUError("mineru-api returned no middle_json")

        try:
            parsed = json.loads(middle_json)
        except json.JSONDecodeError as exc:
            raise MinerUError("mineru-api did not return a proper json") from exc

        if not isinstance(parsed, dict):
            raise MinerUError("mineru-api returned a non-object middle-json")
        return parsed
