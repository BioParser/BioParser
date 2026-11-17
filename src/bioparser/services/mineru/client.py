"""Client for mineru-api's Tasks API (MinerU 4.1 and later): one PDF in, its middle.json out.

MinerU 4.1 removed ``POST /v1/file_parse`` from mineru-api; only a separate wrapper service
serves it now. One parse is one task:

1. ``POST /v1/tasks`` names the PDF (file name, size, SHA-256), the tier and the OCR mode.
2. ``PUT`` sends the PDF to the upload URL in the reply. A reply without one means that
   mineru-api already holds those bytes (same SHA-256), so nothing is sent.
3. ``GET /v1/tasks/{task_id}`` is polled until the file is completed, failed or canceled.
4. ``GET`` on the file's ``result_url`` returns a ZIP; its ``middle_json.json`` is the result.

A task left unfinished, by the deadline, an error or a cancellation of the caller, is
cancelled in mineru-api with ``POST /v1/tasks/{task_id}/cancel``.

``MinerUClient`` is async. It is the one client for the parser and the API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import IO, Any, Literal, get_args
from urllib.parse import quote

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bioparser.hashing import sha256_bytes
from bioparser.logging_config import stopwatch

# Parse quality. Basic is layout, OCR, formula and table small models with no VLM (3.x's
# pipeline backend). Standard adds MinerU's VLM, Advanced runs it on every block. The service
# must serve the tier asked for: its --tier is a ceiling that serves itself and the tiers
# below it.
type MinerUTier = Literal["flash", "basic", "standard", "advanced"]
MINERU_TIERS: tuple[MinerUTier, ...] = get_args(MinerUTier.__value__)
DEFAULT_TIER: MinerUTier = "basic"
OCR_MODE = "auto"

_MIB = 1024 * 1024
# The result ZIP holds the page images as well as middle_json.json. It is spooled to a
# temporary file past _SPOOL_MEMORY_BYTES, so this caps disk and transfer, not memory.
MAX_RESULT_BYTES = 256 * _MIB
# middle_json.json is held in memory and decoded, so it has its own cap. 4.2.3 writes it with
# indent=2, about 1.7 times the JSON 4.0.12 sent inline (then capped at 32 MiB), so this keeps
# that ceiling. A 20 MiB PDF can yield a middle.json far larger than itself: refuse it rather
# than discover the ceiling as an OOMKill.
MAX_MIDDLE_JSON_BYTES = 64 * _MIB
# The longest the client waits for one parse, time queued in mineru-api included. The images
# run mineru-api with a --task-timeout above this, so the client gives up and cancels first:
# a task that reaches mineru-api's own limit makes the service refuse all work until it restarts.
MAX_WAIT_SECONDS = 3600
# One request, not the whole parse: the parse is bounded by its deadline.
_REQUEST_TIMEOUT_S = 60.0
# A cancel or a health probe is best effort and must not hold the caller for long.
_CANCEL_TIMEOUT_S = 5.0
# Polls start fast, so a short parse is not held up, and back off to this interval. The
# interval stays below the 5 s keep-alive of the uvicorn in mineru-api. Thus a poll does not
# use a connection at the time that the server closes it.
_FIRST_POLL_S = 0.5
_MAX_POLL_S = 2.0
_SPOOL_MEMORY_BYTES = 8 * _MIB
# A task reply describes one file and stays small.
_REPLY_BYTES = 1 * _MIB
# An error reply is read only for its V1 code, which fits well within this.
_ERROR_BODY_BYTES = 64 * 1024
_CODE = re.compile(r"[a-z_]{1,64}")
_TASKS = "/v1/tasks"
_HEALTH = "/v1/health"
_MIDDLE_JSON_MEMBER = "middle_json.json"
# Client errors that are load, not the request, so a later retry can succeed.
_RETRIED_STATUSES = frozenset({408, 429})
# After the task exists, these mean it is gone or ended (mineru-api restarted, the upload
# window or the result retention ran out, someone cancelled it), not that the request is wrong.
_TASK_GONE_STATUSES = _RETRIED_STATUSES | {404, 409, 410}
# mineru-api judged the PDF itself: the same PDF fails the same way.
_INPUT_ERROR_CODES = frozenset(
    {
        "file_corrupted",
        "file_encrypted",
        "file_too_large",
        "file_type_unsupported",
        "resource_limit",
    }
)

type FileStatus = Literal["waiting-file", "pending", "running", "completed", "failed", "canceled"]
_FINAL: frozenset[FileStatus] = frozenset({"completed", "failed", "canceled"})
# The file had not started to parse: waiting for its bytes, or queued behind other parses.
_NOT_STARTED: frozenset[FileStatus] = frozenset({"waiting-file", "pending"})

LOGGER = logging.getLogger(__name__)


class MinerUError(RuntimeError):
    """mineru-api failed the parse, was unreachable, or was busy. The same request may succeed later.

    A ``parse_failed`` file is in this class: mineru-api gives that code to a crash or a lack of
    memory as well as to a PDF it could not read.
    """


class MinerUPermanentError(MinerUError):
    """The same request would fail the same way: mineru-api refused it (a 4xx such as a tier it
    does not serve), named a URL outside its own origin, or its result is over a size cap."""


class MinerUInputError(MinerUPermanentError):
    """mineru-api judged the PDF unreadable: corrupted, encrypted, empty, or over its limits."""


class MinerUTimeoutError(MinerUError):
    """The parse ran past its wait; its task was cancelled.

    A caller killed before it can cancel leaves the task running in mineru-api until the task
    ends or reaches mineru-api's --task-timeout.
    """


# hide_input_in_errors: a ValidationError must not quote mineru-api's reply.
_REPLY_CONFIG = ConfigDict(extra="ignore", hide_input_in_errors=True, frozen=True)


class _FileError(BaseModel):
    """The code of a failed file. Its message is never kept: it may quote the PDF."""

    model_config = _REPLY_CONFIG

    code: str = ""


class _Upload(BaseModel):
    model_config = _REPLY_CONFIG

    url: str
    headers: dict[str, str] = Field(default_factory=dict)


class _TaskFile(BaseModel):
    model_config = _REPLY_CONFIG

    status: FileStatus
    upload: _Upload | None = None
    result_url: str | None = None
    error: _FileError | None = None


class _Task(BaseModel):
    """A create or detail reply. The client sends one file, so a reply has one file."""

    model_config = _REPLY_CONFIG

    task_id: str = Field(min_length=1)
    files: list[_TaskFile] = Field(min_length=1, max_length=1)


@dataclass(slots=True)
class _Attempt:
    """One parse: what it read from mineru-api, for its log record, and how far its task got."""

    status_code: int | None = None
    size: int = 0
    requests: int = 0
    task_id: str | None = None
    file_status: FileStatus = "waiting-file"


@contextmanager
def _logged(attempt: _Attempt) -> Iterator[None]:
    """One record per parse: status_code (of the last response, None if none arrived),
    bytes_read (decoded body bytes of all its requests), requests and latency_ms. Only sizes
    are logged."""
    elapsed_ms = stopwatch()
    try:
        yield
    except BaseException as exc:
        fields = _fields(attempt, elapsed_ms()) | {"error": type(exc).__name__}
        LOGGER.warning("mineru-api request failed", extra=fields)
        raise
    LOGGER.info("mineru-api request completed", extra=_fields(attempt, elapsed_ms()))


def _fields(attempt: _Attempt, latency_ms: float) -> dict[str, object]:
    return {
        "status_code": attempt.status_code,
        "bytes_read": attempt.size,
        "requests": attempt.requests,
        "latency_ms": latency_ms,
    }


def _task_path(task_id: str) -> str:
    return f"{_TASKS}/{quote(task_id, safe='')}"


def _transport_error(exc: httpx2.HTTPError) -> MinerUError:
    return MinerUError(f"mineru-api unreachable: {type(exc).__name__}")


def _allowed_code(value: object) -> str:
    """``value`` if it is a V1 error code (``[a-z_]{1,64}``), else ``unknown``.
    Error messages are never kept: they may quote the PDF."""
    return value if isinstance(value, str) and _CODE.fullmatch(value) else "unknown"


def _code_from_error_body(body: bytes | None) -> str:
    """``error.code`` of a V1 error body. The body may be cut off, so this never raises."""
    if body is None:
        return "unknown"
    try:
        payload = json.loads(body)
    except ValueError:
        return "unknown"
    error = payload.get("error") if isinstance(payload, dict) else None
    return _allowed_code(error.get("code") if isinstance(error, dict) else None)


def _http_error(status_code: int, code: str, retried: frozenset[int]) -> MinerUError:
    message = f"mineru-api returned {status_code} ({code})"
    if 400 <= status_code < 500 and status_code not in retried:
        return MinerUPermanentError(message)
    return MinerUError(message)


def _file_error(error: _FileError | None) -> MinerUError:
    code = _allowed_code(error.code if error is not None else None)
    message = f"mineru-api parse failed ({code})"
    if code in _INPUT_ERROR_CODES:
        return MinerUInputError(message)
    # Every other code may succeed later. task_timeout as well: once one task reaches
    # --task-timeout, mineru-api fails each task it starts after that with this code until it
    # restarts, so this PDF was not judged. The retry waits at the worker's health check.
    return MinerUError(message)


def _task_from(body: bytes) -> _Task:
    try:
        return _Task.model_validate_json(body)
    except ValidationError as exc:
        raise MinerUError("mineru-api returned an unexpected task reply") from exc


async def _read(response: httpx2.Response, attempt: _Attempt, limit: int) -> bytes | None:
    """The body, or None as soon as it passes ``limit`` bytes; the rest is not read."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        attempt.size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _middle_json_from_zip(archive_file: IO[bytes]) -> dict[str, Any]:
    """``middle_json.json`` of the result ZIP, as one non-empty JSON object.

    Blocking (file reads, inflate, JSON decode): run it in a thread.
    """
    try:
        with zipfile.ZipFile(archive_file) as archive:
            try:
                member = archive.getinfo(_MIDDLE_JSON_MEMBER)
            except KeyError:
                raise MinerUError("mineru-api result has no middle_json.json") from None
            # zipfile does not inflate more than the declared size of a member. Thus this
            # check limits the memory that the read uses.
            if member.file_size > MAX_MIDDLE_JSON_BYTES:
                raise MinerUPermanentError(
                    f"mineru-api middle_json.json exceeded {MAX_MIDDLE_JSON_BYTES} bytes"
                )
            raw = archive.read(member)
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError) as exc:
        raise MinerUError("mineru-api returned an unreadable result ZIP") from exc
    try:
        middle = json.loads(raw)
    except ValueError as exc:
        raise MinerUError("mineru-api returned a non-JSON middle_json.json") from exc
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
        # The whole parse: create, upload, time queued and parsing, and the download.
        self._wait_s = min(timeout, MAX_WAIT_SECONDS)
        self._tier: MinerUTier = tier
        # mineru-api is an internal service: the PDF must not go through a proxy from the
        # environment, nor follow a redirect to another host.
        self._http = httpx2.AsyncClient(
            base_url=base_url,
            timeout=httpx2.Timeout(_REQUEST_TIMEOUT_S, connect=connect_timeout),
            trust_env=False,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def parse(self, *, filename: str, content: bytes) -> dict[str, Any]:
        """Parse one PDF in mineru-api and return its middle.json object."""
        if not content:
            # mineru-api refuses a file of zero bytes as a bad request, not as a bad PDF.
            raise MinerUInputError("the PDF is empty")
        attempt = _Attempt()
        with _logged(attempt):
            try:
                async with asyncio.timeout(self._wait_s):
                    return await self._run(attempt, filename, content)
            except TimeoutError as exc:
                await self._cancel(attempt)
                # The wait counts time queued behind other parses. A file that never started
                # was not judged, so it is retried; one that ran out of time would run out again.
                if attempt.file_status in _NOT_STARTED:
                    raise MinerUError("mineru-api did not start the parse within its wait") from exc
                raise MinerUTimeoutError(
                    "mineru-api did not finish the parse within its wait"
                ) from exc
            except httpx2.HTTPError as exc:
                await self._cancel(attempt)
                raise _transport_error(exc) from exc
            except BaseException:
                # An error, or the caller's own cancellation: do not leave the task running.
                await self._cancel(attempt)
                raise

    async def _run(self, attempt: _Attempt, filename: str, content: bytes) -> dict[str, Any]:
        created = await self._create(attempt, filename, content)
        upload = created.files[0].upload
        if upload is not None:
            await self._send(
                attempt,
                "PUT",
                self._same_origin(upload.url),
                content=content,
                headers=upload.headers,
                retried=_TASK_GONE_STATUSES,
            )
        file = await self._wait_for_file(attempt, created.task_id)
        if file.status == "failed":
            raise _file_error(file.error)
        if file.status == "canceled":
            raise MinerUError("mineru-api parse canceled")
        if file.result_url is None:
            raise MinerUError("mineru-api returned no result_url")
        return await self._download(attempt, self._same_origin(file.result_url))

    async def _create(self, attempt: _Attempt, filename: str, content: bytes) -> _Task:
        checksum = await asyncio.to_thread(sha256_bytes, content)
        request = {
            "files": [{"file_name": filename, "bytes": len(content), "sha256sum": checksum}],
            "tier": self._tier,
            "ocr_mode": OCR_MODE,
            # A ZIP of markdown.md, middle_json.json, structured_content.json and images/.
            "output_formats": ["default"],
        }
        task = _task_from(await self._send(attempt, "POST", _TASKS, json=request))
        attempt.task_id = task.task_id
        attempt.file_status = task.files[0].status
        return task

    async def _wait_for_file(self, attempt: _Attempt, task_id: str) -> _TaskFile:
        """Poll until the file is completed, failed or canceled. The parse's timeout ends it."""
        path = _task_path(task_id)
        delay = _FIRST_POLL_S
        while True:
            body = await self._send(attempt, "GET", path, retried=_TASK_GONE_STATUSES)
            file = _task_from(body).files[0]
            attempt.file_status = file.status
            if file.status in _FINAL:
                return file
            await asyncio.sleep(delay)
            delay = min(delay * 2, _MAX_POLL_S)

    async def _send(
        self,
        attempt: _Attempt,
        method: str,
        url: str | httpx2.URL,
        *,
        retried: frozenset[int] = _RETRIED_STATUSES,
        **kwargs: Any,
    ) -> bytes:
        """One request; its body if the status is 2xx, else the MinerUError it stands for."""
        attempt.requests += 1
        async with self._http.stream(method, url, **kwargs) as response:
            attempt.status_code = response.status_code
            if response.is_success:
                body = await _read(response, attempt, _REPLY_BYTES)
                if body is None:
                    raise MinerUError(f"mineru-api reply exceeded {_REPLY_BYTES} bytes")
                return body
            # An error body is judged by its status once it passes _ERROR_BODY_BYTES.
            error_body = await _read(response, attempt, _ERROR_BODY_BYTES)
        raise _http_error(response.status_code, _code_from_error_body(error_body), retried)

    async def _download(self, attempt: _Attempt, url: httpx2.URL) -> dict[str, Any]:
        """Spool the result ZIP, abandoning it at the first chunk over MAX_RESULT_BYTES, and
        return its middle.json."""
        attempt.requests += 1
        with tempfile.SpooledTemporaryFile(max_size=_SPOOL_MEMORY_BYTES) as spool:
            async with self._http.stream("GET", url) as response:
                attempt.status_code = response.status_code
                if not response.is_success:
                    body = await _read(response, attempt, _ERROR_BODY_BYTES)
                    raise _http_error(
                        response.status_code, _code_from_error_body(body), _TASK_GONE_STATUSES
                    )
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    attempt.size += len(chunk)
                    if size > MAX_RESULT_BYTES:
                        raise MinerUPermanentError(
                            f"mineru-api result exceeded {MAX_RESULT_BYTES} bytes"
                        )
                    await asyncio.to_thread(spool.write, chunk)
            return await asyncio.to_thread(_middle_json_from_zip, spool)

    def _same_origin(self, url: str) -> httpx2.URL:
        """``url``, resolved against the base URL, if it is on mineru-api's own origin. The PDF
        and the result must not go to, or come from, another host."""
        try:
            resolved = self._http.base_url.join(url)
        except httpx2.InvalidURL as exc:
            raise MinerUPermanentError("mineru-api named an invalid URL") from exc
        if resolved.origin != self._http.base_url.origin:
            raise MinerUPermanentError("mineru-api named a URL outside its own origin")
        return resolved

    async def _cancel(self, attempt: _Attempt) -> None:
        """Cancel the parse's task in mineru-api, if it has one that has not ended. Best effort:
        never raises an error of its own."""
        if attempt.task_id is None or attempt.file_status in _FINAL:
            return
        try:
            response = await self._http.post(
                f"{_task_path(attempt.task_id)}/cancel", timeout=_CANCEL_TIMEOUT_S
            )
        except httpx2.HTTPError as exc:
            LOGGER.warning("mineru-api task cancel failed", extra={"error": type(exc).__name__})
            return
        # 404: mineru-api no longer has the task (it restarted). 409: the task ended first; its
        # result is collected with mineru-api's retention.
        if not response.is_success and response.status_code not in {404, 409}:
            LOGGER.warning(
                "mineru-api task cancel failed", extra={"status_code": response.status_code}
            )

    @staticmethod
    async def ping(base_url: str) -> bool:
        """True when ``GET /v1/health`` answers 200."""
        async with httpx2.AsyncClient(
            base_url=base_url, timeout=_CANCEL_TIMEOUT_S, trust_env=False
        ) as http:
            try:
                return (await http.get(_HEALTH)).status_code == 200
            except httpx2.HTTPError:
                return False
