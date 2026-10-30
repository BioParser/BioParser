"""Clients for mineru-api's ``POST /v1/file_parse``: one PDF in, its middle.json out.

``MinerUClient`` is async. It is the one client for the parser and the API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, Literal, get_args
from urllib.parse import quote

import httpx2

from bioparser.logging_config import stopwatch

# Parse quality. Basic is layout, OCR, formula and table small models with no VLM (3.x's
# pipeline backend). Standard adds MinerU's VLM, Advanced runs it on every block. The service
# must serve the tier asked for (mineru-api --tier).
type MinerUTier = Literal["flash", "basic", "standard", "advanced"]
MINERU_TIERS: tuple[MinerUTier, ...] = get_args(MinerUTier.__value__)
DEFAULT_TIER: MinerUTier = "basic"
OCR_MODE = "auto"
# A 20 MiB PDF can yield a middle_json far larger than itself
# Refuse the body rather than discover the ceiling as an OOMKill.
# TODO: We should probably make these bigger?
# TODO: implement in config?
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
# mineru-api waits at most an hour for one synchronous parse.
MAX_WAIT_SECONDS = 3600
# The read timeout runs this much past the server-side wait, so the server's own 202 arrives first.
_READ_MARGIN_S = 10.0
# A cancel or a health probe is best effort and must not hold the caller for a whole parse wait.
_CANCEL_TIMEOUT_S = 5.0
_READY_POLL_S = 5.0
_FILE_PARSE = "/v1/file_parse"
_HEALTH = "/v1/health"
# Replies that carry a parse: done (200), not finished when the wait ran out (202), failed (409).
_PARSE_STATUSES = frozenset({200, 202, 409})
# Client errors that are load, not the request, so a later retry can succeed.
_RETRIED_CLIENT_ERRORS = frozenset({408, 429})
# An error reply is read only for its V1 code, which fits well within this.
_ERROR_BODY_BYTES = 64 * 1024
_CODE = re.compile(r"[a-z_]{1,64}")

LOGGER = logging.getLogger(__name__)


class MinerUError(RuntimeError):
    """mineru-api failed the parse, was unreachable, or was busy. The same request may succeed later.

    4.0.12 reports every failed parse, an unreadable PDF included, as ``parse_failed``.
    """


class MinerUPermanentError(MinerUError):
    """The same request would fail the same way: mineru-api refused it (a 4xx such as a tier it
    does not serve), or its result is over MAX_RESPONSE_BYTES."""


class MinerUTimeoutError(MinerUError):
    """The parse ran past its wait; its task was cancelled.

    Only a 202 names the task. A caller that dies or stops waiting first leaves the task running
    in mineru-api until it ends on its own.
    """


@dataclass(slots=True)
class _Reply:
    """One mineru-api reply: its status, the body bytes seen so far, and the chunks kept."""

    status_code: int | None = None
    size: int = 0
    chunks: list[bytes] = field(default_factory=list)

    def add(self, chunk: bytes) -> None:
        """Keep a body chunk. An error body is judged by its status once it passes
        _ERROR_BODY_BYTES, and any body over MAX_RESPONSE_BYTES is refused."""
        self.size += len(chunk)
        if self.status_code not in _PARSE_STATUSES and self.size > _ERROR_BODY_BYTES:
            self.raise_unless_parse_status()
        if self.size > MAX_RESPONSE_BYTES:
            raise MinerUPermanentError(f"mineru-api response exceeded {MAX_RESPONSE_BYTES} bytes")
        self.chunks.append(chunk)

    def raise_unless_parse_status(self) -> None:
        """Raise unless the status is 200, 202 or 409. This only judges the status;
        ``_middle_json`` reads the body of those three."""
        if self.status_code in _PARSE_STATUSES:
            return
        message = f"mineru-api returned {self.status_code} ({_code_from_error_body(self.chunks)})"
        status_code = self.status_code or 0
        if 400 <= status_code < 500 and status_code not in _RETRIED_CLIENT_ERRORS:
            raise MinerUPermanentError(message)
        raise MinerUError(message)


@contextmanager
def _logged() -> Iterator[_Reply]:
    """One record per request: status_code (None if no response arrived), bytes_read
    (decoded body bytes) and latency_ms. Only sizes are logged."""
    reply = _Reply()
    elapsed_ms = stopwatch()
    try:
        yield reply
    except BaseException as exc:
        fields = _fields(reply, elapsed_ms()) | {"error": type(exc).__name__}
        LOGGER.warning("mineru-api request failed", extra=fields)
        raise
    LOGGER.info("mineru-api request completed", extra=_fields(reply, elapsed_ms()))


def _fields(reply: _Reply, latency_ms: float) -> dict[str, object]:
    return {
        "status_code": reply.status_code,
        "bytes_read": reply.size,
        "latency_ms": latency_ms,
    }


def _params(wait_s: float, tier: MinerUTier) -> dict[str, str]:
    return {
        "tier": tier,
        "ocr_mode": OCR_MODE,
        "output_formats": "middle_json",
        "wait_timeout": str(min(MAX_WAIT_SECONDS, max(1, math.ceil(wait_s)))),
    }


def _timeout(wait_s: float, connect_timeout: float) -> httpx2.Timeout:
    return httpx2.Timeout(min(wait_s, MAX_WAIT_SECONDS) + _READ_MARGIN_S, connect=connect_timeout)


def _files(filename: str, content: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {"files": (filename, content, "application/pdf")}


def _transport_error(exc: httpx2.HTTPError) -> MinerUError:
    # The read timeout runs past the server's own wait, so running out of it means the server
    # stopped answering, not that the parse was slow.
    return MinerUError(f"mineru-api unreachable: {type(exc).__name__}")


def _allowed_code(value: object) -> str:
    """``value`` if it is a V1 error code (``[a-z_]{1,64}``), else ``unknown``.
    Error messages are never kept: they may quote the PDF."""
    return value if isinstance(value, str) and _CODE.fullmatch(value) else "unknown"


def _code_from_error_body(chunks: list[bytes]) -> str:
    """``error.code`` of a V1 error body. The body may be cut off, so this never raises."""
    try:
        payload = json.loads(b"".join(chunks))
    except ValueError:
        return "unknown"
    error = payload.get("error") if isinstance(payload, dict) else None
    return _allowed_code(error.get("code") if isinstance(error, dict) else None)


def _decode_json_object(body: bytes) -> dict[str, Any]:
    """The finished body as one JSON object. Anything else raises MinerUError."""
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise MinerUError("mineru-api returned a non-JSON body") from exc
    if not isinstance(payload, dict):
        raise MinerUError("mineru-api returned a non-object body")
    return payload


def _task_path(payload: dict[str, Any]) -> str | None:
    """Path that cancels the task a 202 reply describes, if it named one."""
    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return None
    return f"/v1/tasks/{quote(task_id, safe='')}"


def _middle_json(status_code: int, payload: dict[str, Any]) -> dict[str, Any]:
    if status_code == 202:
        # The wait counts time queued behind other parses. A task that never started was not
        # judged, so it is retried; one that ran out of time would run out again.
        if payload.get("status") == "queued":
            raise MinerUError("mineru-api did not start the parse within its wait")
        raise MinerUTimeoutError("mineru-api did not finish the parse within its wait")
    files = payload.get("files")
    if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict):
        raise MinerUError("mineru-api returned no single file result")
    result = files[0]
    if status_code == 409:
        error = result.get("error")
        code = _allowed_code(error.get("code") if isinstance(error, dict) else None)
        status = "canceled" if payload.get("status") == "canceled" else "failed"
        raise MinerUError(f"mineru-api parse {status} ({code})")
    content = result.get("content")
    middle = content.get("middle_json") if isinstance(content, dict) else None
    if not isinstance(middle, dict) or not middle:
        raise MinerUError("mineru-api returned no middle_json")
    return middle


class MinerUClient:
    def __init__(
        self,
        base_url: str,
        timeout: float,
        connect_timeout: float,
        *,
        tier: MinerUTier = DEFAULT_TIER,
    ) -> None:
        self._wait_s = timeout
        self._tier: MinerUTier = tier
        # mineru-api is an internal service: the PDF must not go through a proxy from the environment.
        self._http = httpx2.AsyncClient(
            base_url=base_url, timeout=_timeout(timeout, connect_timeout), trust_env=False
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def parse(self, *, filename: str, content: bytes) -> dict[str, Any]:
        """POST /v1/file_parse and return the parsed middle.json object."""
        try:
            status_code, body = await self._post(filename, content)
            payload = _decode_json_object(body)
            if status_code == 202:
                await self._cancel(payload)
        except httpx2.HTTPError as exc:
            raise _transport_error(exc) from exc
        return _middle_json(status_code, payload)

    async def _post(self, filename: str, content: bytes) -> tuple[int, bytes]:
        """Stream the response; abandon a body over MAX_RESPONSE_BYTES at the first chunk past it."""
        with _logged() as reply:
            async with self._http.stream(
                "POST",
                _FILE_PARSE,
                params=_params(self._wait_s, self._tier),
                files=_files(filename, content),
            ) as response:
                reply.status_code = response.status_code
                async for chunk in response.aiter_bytes():
                    reply.add(chunk)
            reply.raise_unless_parse_status()
        return response.status_code, b"".join(reply.chunks)

    async def _cancel(self, payload: dict[str, Any]) -> None:
        if (path := _task_path(payload)) is None:
            return
        try:
            await self._http.delete(path, timeout=_CANCEL_TIMEOUT_S)
        except httpx2.HTTPError as exc:
            LOGGER.warning("mineru-api task cancel failed", extra={"error": type(exc).__name__})


async def wait_until_ready(base_url: str, timeout_s: float) -> None:
    """Return once ``GET /v1/health`` answers 200; raise MinerUError after about timeout_s.

    mineru-api answers 503 until its models are loaded, minutes on GPU. Waiting here holds the
    job instead of spending its retries on a service that is restarting.
    """
    deadline = time.monotonic() + timeout_s
    async with httpx2.AsyncClient(
        base_url=base_url, timeout=_CANCEL_TIMEOUT_S, trust_env=False
    ) as http:
        while True:
            with suppress(httpx2.HTTPError):
                if (await http.get(_HEALTH)).status_code == 200:
                    return
            if time.monotonic() + _READY_POLL_S > deadline:
                raise MinerUError("mineru-api is not ready")
            await asyncio.sleep(_READY_POLL_S)
