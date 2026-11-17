"""MinerUClient against a misbehaving mineru-api Tasks API."""

import asyncio
import hashlib
import itertools
import json
import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx2
import pytest
from stubs import fake_mineru, result_zip

from bioparser import logging_config
from bioparser.logging_config import JsonFormatter
from bioparser.services.mineru import client as client_module
from bioparser.services.mineru.client import (
    MAX_WAIT_SECONDS,
    MinerUClient,
    MinerUError,
    MinerUInputError,
    MinerUPermanentError,
    MinerUTier,
    MinerUTimeoutError,
)

BASE = "http://mineru:8000"
PDF = b"%PDF-1.4\n%%EOF\n"
MIDDLE = {"schema": "docvortex.middle", "pages": [{"page_idx": 0, "blocks": []}]}
UPLOAD_URL = f"{BASE}/v1/tasks/job_1/uploads/file-1"
RESULT_URL = f"{BASE}/v1/files/file-1/content"
MIB = 1024 * 1024


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "_FIRST_POLL_S", 0.001)
    monkeypatch.setattr(client_module, "_MAX_POLL_S", 0.002)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _error(code: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": "secret text"}}


class FakeTasks:
    """A mineru-api Tasks API for MockTransport.

    POST /v1/tasks answers ``created``; the upload PUT answers 200; each GET of the task shows
    the next of ``statuses`` (the last repeats) with ``file`` merged into the file entry; the
    result URL answers ``result``; a cancel answers ``cancel_status``. Each attribute can be
    replaced with a ready ``httpx2.Response``.
    """

    def __init__(
        self,
        *statuses: str,
        result: bytes | httpx2.Response | None = None,
        upload: bool = True,
        **file: object,
    ) -> None:
        self.statuses = statuses or ("completed",)
        self.file = file
        self.created: dict[str, Any] | httpx2.Response = _created(upload=upload)
        self.upload: httpx2.Response | None = None
        self.detail: httpx2.Response | None = None
        self.result = result_zip(MIDDLE) if result is None else result
        self.cancel_status = 200
        self.requests: list[httpx2.Request] = []
        self.sent_bytes = 0
        self._polls = 0

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self._answer(request)
        try:
            self.sent_bytes += len(response.content)
        except httpx2.ResponseNotRead:  # a streamed body, counted by the test that sends it
            pass
        return response

    def _answer(self, request: httpx2.Request) -> httpx2.Response:
        method, path = request.method, request.url.path
        if (method, path) == ("POST", "/v1/tasks"):
            if isinstance(self.created, httpx2.Response):
                return self.created
            return httpx2.Response(202, json=self.created)
        if method == "PUT":
            return self.upload or httpx2.Response(
                200, json={"task_id": "job_1", "file_id": "file-1", "status": "pending"}
            )
        if method == "POST" and path.endswith("/cancel"):
            return httpx2.Response(self.cancel_status, json={})
        if method == "GET" and path == "/v1/files/file-1/content":
            if isinstance(self.result, httpx2.Response):
                return self.result
            return httpx2.Response(200, content=self.result)
        if method == "GET" and path.startswith("/v1/tasks/"):
            if self.detail is not None:
                return self.detail
            status = self.statuses[min(self._polls, len(self.statuses) - 1)]
            self._polls += 1
            task_id = self.created["task_id"] if isinstance(self.created, dict) else "job_1"
            return httpx2.Response(200, json=_detail(status, task_id=task_id, **self.file))
        return httpx2.Response(404, json=_error("not_found"))

    def seen(self) -> list[str]:
        return [f"{r.method} {r.url.raw_path.decode()}" for r in self.requests]


def _created(*, upload: bool = True, task_id: str = "job_1") -> dict[str, Any]:
    file: dict[str, Any] = {"file_id": "file-1", "file_name": "a.pdf", "status": "pending"}
    if upload:
        file["status"] = "waiting-file"
        file["upload"] = {
            "url": UPLOAD_URL,
            "headers": {"Content-Type": "application/octet-stream"},
            "expires_at": 1,
        }
    return {"task_id": task_id, "status": file["status"], "files": [file]}


def _detail(status: str, *, task_id: str = "job_1", **file: object) -> dict[str, Any]:
    entry: dict[str, Any] = {"file_id": "file-1", "file_name": "a.pdf", "status": status}
    if status == "completed":
        entry["result_url"] = RESULT_URL
    return {
        "task_id": task_id,
        "status": status,
        "created_at": "2026-11-17T00:00:00Z",
        "output_formats": ["default"],
        "progress": {"completed": 0, "failed": 0, "total": 1},
        "files": [entry | file],
    }


def _client(fake: object, tier: MinerUTier = "basic", wait_s: float = 5.0) -> MinerUClient:
    client = MinerUClient(BASE, wait_s, 1.0, tier=tier)
    client._http = httpx2.AsyncClient(
        transport=httpx2.MockTransport(fake),  # type: ignore[arg-type]
        base_url=BASE,
    )
    return client


def _parse(
    fake: object, pdf: bytes = PDF, tier: MinerUTier = "basic", wait_s: float = 5.0
) -> dict[str, Any]:
    """Run one parse and return the middle JSON."""

    async def run() -> dict[str, Any]:
        client = _client(fake, tier, wait_s)
        try:
            return await client.parse(filename="a.pdf", content=pdf)
        finally:
            await client.aclose()

    return asyncio.run(run())


# --- the request ---------------------------------------------------------------


def test_the_task_names_the_pdf_the_tier_and_the_zip_output() -> None:
    fake = FakeTasks()

    assert _parse(fake) == MIDDLE

    create = fake.requests[0]
    assert (create.method, create.url.path, create.url.query) == ("POST", "/v1/tasks", b"")
    assert create.headers["content-type"] == "application/json"
    assert json.loads(create.content) == {
        "files": [
            {"file_name": "a.pdf", "bytes": len(PDF), "sha256sum": hashlib.sha256(PDF).hexdigest()}
        ],
        "tier": "basic",
        "ocr_mode": "auto",
        "output_formats": ["default"],
    }


def test_the_pdf_goes_to_the_upload_url_with_its_headers_then_the_result_is_read() -> None:
    fake = FakeTasks("running", "running", "completed")

    _parse(fake)

    assert fake.seen() == [
        "POST /v1/tasks",
        "PUT /v1/tasks/job_1/uploads/file-1",
        "GET /v1/tasks/job_1",
        "GET /v1/tasks/job_1",
        "GET /v1/tasks/job_1",
        "GET /v1/files/file-1/content",
    ]
    upload = fake.requests[1]
    assert upload.content == PDF
    assert upload.headers["content-type"] == "application/octet-stream"


def test_a_pdf_mineru_already_holds_is_not_sent_again() -> None:
    # Same SHA-256 as an earlier upload: the reply has no upload URL and the file is pending.
    fake = FakeTasks(upload=False)

    assert _parse(fake) == MIDDLE
    assert not any(request.method == "PUT" for request in fake.requests)


def test_the_tier_is_the_one_the_client_was_given() -> None:
    fake = FakeTasks()

    _parse(fake, tier="advanced")

    assert json.loads(fake.requests[0].content)["tier"] == "advanced"


def test_an_empty_pdf_is_an_input_error_and_is_not_sent() -> None:
    fake = FakeTasks()

    with pytest.raises(MinerUInputError, match="empty"):
        _parse(fake, pdf=b"")
    assert fake.requests == []


@pytest.mark.anyio
async def test_the_client_does_not_send_through_a_proxy_or_follow_redirects() -> None:
    # The PDF goes straight to the internal service, whatever HTTP_PROXY says.
    client = MinerUClient(BASE, 5.0, 1.0)
    assert not client._http.trust_env
    assert not client._http.follow_redirects
    await client.aclose()


def test_the_wait_is_capped_at_mineru_s_limit() -> None:
    assert MinerUClient(BASE, 10 * MAX_WAIT_SECONDS, 1.0)._wait_s == MAX_WAIT_SECONDS


@pytest.mark.parametrize("field", ["upload", "result"])
@pytest.mark.parametrize(
    "url", ["http://elsewhere:8000/x", "https://mineru:8000/x", "http://mineru:8001/x"]
)
def test_a_url_on_another_origin_is_refused(field: str, url: str) -> None:
    fake = FakeTasks()
    created = _created()
    if field == "upload":
        created["files"][0]["upload"]["url"] = url
    fake.created = created
    if field == "result":
        fake.file = {"result_url": url}

    with pytest.raises(MinerUPermanentError, match="outside its own origin"):
        _parse(fake)
    # Nothing is sent to, or read from, that host; the task is cancelled unless it has ended.
    assert all(request.url.host == "mineru" for request in fake.requests)
    assert ("POST /v1/tasks/job_1/cancel" in fake.seen()) is (field == "upload")


def test_a_relative_result_url_is_read_from_mineru() -> None:
    fake = FakeTasks(result_url="/v1/files/file-1/content")

    assert _parse(fake) == MIDDLE


# --- errors the service reports ---------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "error", "message"),
    [
        (400, _error("invalid_request"), MinerUPermanentError, "400 \\(invalid_request\\)"),
        (401, _error("invalid_api_key"), MinerUPermanentError, "401 \\(invalid_api_key\\)"),
        (415, _error("invalid_request"), MinerUPermanentError, "415 \\(invalid_request\\)"),
        (429, _error("rate_limited"), MinerUError, "429 \\(rate_limited\\)"),
        (503, _error("server_shutting_down"), MinerUError, "503 \\(server_shutting_down\\)"),
        (503, b"upstream down", MinerUError, "503 \\(unknown\\)"),
    ],
    ids=["tier-not-served", "bad-key", "old-protocol", "busy", "stopping", "down"],
)
def test_a_refused_task_is_judged_by_status_and_code(
    status: int, body: dict[str, Any] | bytes, error: type[MinerUError], message: str
) -> None:
    fake = FakeTasks()
    content = body if isinstance(body, bytes) else json.dumps(body).encode()
    fake.created = httpx2.Response(status, content=content)

    with pytest.raises(MinerUError, match=message) as caught:
        _parse(fake)
    assert type(caught.value) is error
    assert "secret text" not in str(caught.value)
    assert fake.seen() == ["POST /v1/tasks"]  # no task, nothing to cancel


@pytest.mark.parametrize(
    ("step", "status", "code"),
    [
        ("upload", 404, "upload_not_found"),
        ("upload", 409, "file_already_terminal"),
        ("upload", 410, "upload_expired"),
        ("poll", 404, "task_not_found"),
        ("result", 404, "file_not_found"),
    ],
)
def test_a_task_that_is_gone_is_retryable(step: str, status: int, code: str) -> None:
    # mineru-api restarted (tasks live in its memory), or a window or a retention ran out.
    fake = FakeTasks()
    gone = httpx2.Response(status, json=_error(code))
    if step == "upload":
        fake.upload = gone
    elif step == "poll":
        fake.detail = gone
    else:
        fake.result = gone

    with pytest.raises(MinerUError, match=f"{status} \\({code}\\)") as caught:
        _parse(fake)
    assert type(caught.value) is MinerUError


def test_a_large_error_body_is_not_read_to_the_result_cap() -> None:
    # Classified by its status, like any 503, not refused as an oversized result.
    fake = FakeTasks()
    fake.created = httpx2.Response(503, content=b"x" * (4 * MIB))

    with pytest.raises(MinerUError, match="503 \\(unknown\\)") as caught:
        _parse(fake)
    assert type(caught.value) is MinerUError


@pytest.mark.parametrize(
    ("code", "message"),
    [("parse_failed", "parse failed \\(parse_failed\\)"), ("Not a code\n", "unknown")],
    ids=["catch-all", "garbage"],
)
def test_a_failed_parse_is_retryable_and_keeps_only_its_code(code: str, message: str) -> None:
    fake = FakeTasks("failed", error={"code": code, "message": "secret text"})

    with pytest.raises(MinerUError, match=message) as caught:
        _parse(fake)
    assert type(caught.value) is MinerUError
    assert "secret text" not in str(caught.value)
    # A file that ended is not cancelled.
    assert not any(request.url.path.endswith("/cancel") for request in fake.requests)


@pytest.mark.parametrize(
    "code",
    [
        "file_corrupted",
        "file_encrypted",
        "file_too_large",
        "file_type_unsupported",
        "resource_limit",
    ],
)
def test_a_pdf_mineru_cannot_read_is_an_input_error(code: str) -> None:
    fake = FakeTasks("failed", error={"code": code, "message": "secret text"})

    with pytest.raises(MinerUInputError, match=f"\\({code}\\)"):
        _parse(fake)


def test_a_task_failed_by_mineru_s_stopped_state_is_retryable() -> None:
    # After any task reaches --task-timeout, mineru-api fails each task it starts with
    # task_timeout until it restarts: this PDF was never judged, so the job must not end.
    fake = FakeTasks("pending", "failed", error={"code": "task_timeout", "message": "x"})

    with pytest.raises(MinerUError, match="task_timeout") as caught:
        _parse(fake)
    assert type(caught.value) is MinerUError


def test_a_file_cancelled_in_mineru_is_retryable() -> None:
    with pytest.raises(MinerUError, match="canceled") as caught:
        _parse(FakeTasks("running", "canceled"))
    assert type(caught.value) is MinerUError


# --- the wait ------------------------------------------------------------------------


def test_a_parse_still_running_after_the_wait_is_cancelled() -> None:
    fake = FakeTasks("running")
    fake.created = _created(task_id="job/../x")

    with pytest.raises(MinerUTimeoutError, match="did not finish"):
        _parse(fake, wait_s=0.2)
    # The task ID is one path segment, whatever it holds.
    assert fake.seen()[-1] == "POST /v1/tasks/job%2F..%2Fx/cancel"


@pytest.mark.parametrize("status", ["waiting-file", "pending"])
def test_a_parse_that_never_started_is_cancelled_and_retryable(status: str) -> None:
    # The wait counts time queued behind other parses: this PDF was never judged.
    fake = FakeTasks(status)

    with pytest.raises(MinerUError, match="did not start") as caught:
        _parse(fake, wait_s=0.2)
    assert type(caught.value) is MinerUError
    assert fake.seen()[-1] == "POST /v1/tasks/job_1/cancel"


def test_a_failed_cancel_still_reports_the_timeout() -> None:
    fake = FakeTasks("running")
    fake.cancel_status = 500

    with pytest.raises(MinerUTimeoutError):
        _parse(fake, wait_s=0.2)


@pytest.mark.anyio
async def test_a_caller_that_stops_waiting_cancels_the_task() -> None:
    fake = FakeTasks("running")
    client = _client(fake)

    # TimeoutError means the CancelledError propagated; a swallowed one would not time out.
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            await client.parse(filename="a.pdf", content=PDF)
    await client.aclose()

    assert fake.seen()[-1] == "POST /v1/tasks/job_1/cancel"


@pytest.mark.parametrize(
    "exc",
    [httpx2.ReadTimeout("slow"), httpx2.ConnectTimeout("no route"), httpx2.ConnectError("refused")],
    ids=["read-timeout", "connect-timeout", "refused"],
)
def test_transport_failures_are_retryable(exc: httpx2.HTTPError) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise exc

    with pytest.raises(MinerUError, match="unreachable") as caught:
        _parse(handler)
    assert type(caught.value) is MinerUError


def test_a_transport_failure_after_the_task_exists_cancels_it() -> None:
    fake = FakeTasks()
    calls = itertools.count()

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET" and next(calls) == 0:
            raise httpx2.ReadError("reset")
        return fake(request)

    with pytest.raises(MinerUError, match="unreachable: ReadError"):
        _parse(handler)
    assert fake.seen()[-1] == "POST /v1/tasks/job_1/cancel"


# --- replies that do not hold up ---------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'"not an object"',
        b"{}",
        b'{"task_id": "job_1", "files": []}',
        b'{"task_id": "job_1", "files": [{}, {}]}',
        b'{"task_id": "", "files": [{"file_id": "f", "status": "pending"}]}',
        b'{"task_id": "job_1", "files": [{"file_id": "f", "status": "sleeping"}]}',
    ],
    ids=[
        "not-json",
        "body-str",
        "no-files",
        "zero-files",
        "two-files",
        "empty-id",
        "unknown-status",
    ],
)
def test_malformed_task_replies_are_retryable_errors(body: bytes) -> None:
    fake = FakeTasks()
    fake.created = httpx2.Response(202, content=body)

    with pytest.raises(MinerUError, match="unexpected task reply") as caught:
        _parse(fake)
    assert type(caught.value) is MinerUError


def test_a_completed_file_without_a_result_url_is_retryable() -> None:
    fake = FakeTasks()
    detail = _detail("completed")
    del detail["files"][0]["result_url"]
    fake.detail = httpx2.Response(200, json=detail)

    with pytest.raises(MinerUError, match="no result_url"):
        _parse(fake)


@pytest.mark.parametrize(
    "result",
    [
        b"not a zip",
        result_zip(None),
        result_zip({}),
        result_zip(["not", "an", "object"]),  # type: ignore[arg-type]
        result_zip(MIDDLE)[:-30],
    ],
    ids=["not-zip", "no-member", "empty-object", "not-object", "cut-off"],
)
def test_a_result_without_a_usable_middle_json_is_retryable(result: bytes) -> None:
    with pytest.raises(MinerUError) as caught:
        _parse(FakeTasks(result=result))
    assert type(caught.value) is MinerUError


def test_a_corrupt_middle_json_member_is_retryable() -> None:
    good = bytearray(result_zip(MIDDLE))
    # Flip a byte inside the deflated middle_json.json: CRC or inflate fails.
    offset = good.index(b"middle_json.json") + len("middle_json.json") + 5
    good[offset] ^= 0xFF

    with pytest.raises(MinerUError) as caught:
        _parse(FakeTasks(result=bytes(good)))
    assert type(caught.value) is MinerUError


def test_an_oversized_middle_json_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # The same PDF gives the same result, so a retry would parse it again for nothing.
    monkeypatch.setattr(client_module, "MAX_MIDDLE_JSON_BYTES", 64)

    with pytest.raises(MinerUPermanentError, match="middle_json.json exceeded 64 bytes"):
        _parse(FakeTasks(result=result_zip({"pad": "x" * 100})))


async def _mib_chunks(count: int) -> AsyncIterator[bytes]:
    for _ in range(count):
        yield b"x" * MIB


def test_an_oversized_result_is_refused_not_buffered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "MAX_RESULT_BYTES", 4 * MIB)
    fake = FakeTasks(result=httpx2.Response(200, content=_mib_chunks(8)))

    with pytest.raises(MinerUPermanentError, match=f"result exceeded {4 * MIB} bytes"):
        _parse(fake)


def test_a_large_result_spills_to_disk_and_still_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "_SPOOL_MEMORY_BYTES", 1024)
    middle = {"pages": [{"page_idx": 0, "text": "y" * 50_000}]}

    assert _parse(FakeTasks(result=result_zip(middle))) == middle


# --- logging ---------------------------------------------------------------------------

EXCHANGE_FIELDS = frozenset({"status_code", "bytes_read", "requests", "latency_ms", "error"})


@pytest.fixture
def mineru_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> Iterator[pytest.LogCaptureFixture]:
    """Capture the client's records, with a clock that makes every parse take 250 ms."""
    caplog.set_level(logging.INFO, logger=client_module.__name__)
    monkeypatch.setattr(logging_config, "perf_counter", itertools.count(100.0, 0.25).__next__)
    yield caplog


def _exchanges(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str, dict[str, object]]]:
    """(level, message, fields) of every record the client logged."""
    return [
        (
            record.levelno,
            record.getMessage(),
            {k: v for k, v in vars(record).items() if k in EXCHANGE_FIELDS},
        )
        for record in caplog.records
        if record.name == client_module.__name__
    ]


def test_a_completed_parse_logs_one_record_with_status_bytes_requests_and_latency(
    mineru_log: pytest.LogCaptureFixture,
) -> None:
    fake = FakeTasks("running", "completed")

    _parse(fake)

    assert _exchanges(mineru_log) == [
        (
            logging.INFO,
            "mineru-api request completed",
            {
                "status_code": 200,
                "bytes_read": fake.sent_bytes,
                "requests": 5,
                "latency_ms": 250.0,
            },
        )
    ]


def test_the_cancel_of_a_task_mineru_lost_logs_no_cancel_failure(
    mineru_log: pytest.LogCaptureFixture,
) -> None:
    fake = FakeTasks()
    fake.detail = httpx2.Response(404, json=_error("task_not_found"))
    fake.cancel_status = 404

    with pytest.raises(MinerUError, match="404 \\(task_not_found\\)"):
        _parse(fake)

    assert fake.seen()[-1] == "POST /v1/tasks/job_1/cancel"
    assert [message for _level, message, _fields in _exchanges(mineru_log)] == [
        "mineru-api request failed"
    ]


def _upstream_down(_request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(503, content=b"upstream down")


def _refused(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("connection refused", request=request)


@pytest.mark.parametrize(
    ("handler", "fields"),
    [
        (_upstream_down, {"status_code": 503, "bytes_read": 13, "error": "MinerUError"}),
        (_refused, {"status_code": None, "bytes_read": 0, "error": "MinerUError"}),
    ],
    ids=["non-2xx", "unreachable"],
)
def test_a_failed_parse_logs_one_warning(
    mineru_log: pytest.LogCaptureFixture, handler: object, fields: dict[str, object]
) -> None:
    with pytest.raises(MinerUError):
        _parse(handler)

    assert _exchanges(mineru_log) == [
        (
            logging.WARNING,
            "mineru-api request failed",
            {**fields, "requests": 1, "latency_ms": 250.0},
        )
    ]


def test_an_oversized_result_is_abandoned_at_the_first_chunk_over_the_cap(
    mineru_log: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client_module, "MAX_RESULT_BYTES", 4 * MIB)
    fake = FakeTasks(result=httpx2.Response(200, content=_mib_chunks(64)))

    with pytest.raises(MinerUPermanentError):
        _parse(fake)

    [(level, _message, fields)] = _exchanges(mineru_log)
    assert level == logging.WARNING
    assert fields["error"] == "MinerUPermanentError"
    # Every JSON reply, then the result up to the first chunk past 4 MiB, not all 64 MiB.
    assert fields["bytes_read"] == fake.sent_bytes + 5 * MIB


@pytest.mark.anyio
async def test_a_cancelled_parse_is_logged_and_stays_cancelled(
    mineru_log: pytest.LogCaptureFixture,
) -> None:
    async def never_answers(_request: httpx2.Request) -> httpx2.Response:
        await asyncio.Event().wait()  # until cancelled
        return httpx2.Response(200)

    client = _client(never_answers)

    # TimeoutError means the CancelledError propagated; a swallowed one would not time out.
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await client.parse(filename="a.pdf", content=PDF)
    await client.aclose()

    assert _exchanges(mineru_log) == [
        (
            logging.WARNING,
            "mineru-api request failed",
            {
                "status_code": None,
                "bytes_read": 0,
                "requests": 1,
                "latency_ms": 250.0,
                "error": "CancelledError",
            },
        )
    ]


def test_neither_the_pdf_nor_any_reply_body_is_logged(
    mineru_log: pytest.LogCaptureFixture,
) -> None:
    pdf = b"%PDF-1.4\n% canary-in-the-pdf\n%%EOF\n"
    fake = FakeTasks(result=result_zip({"note": "canary-in-the-body"}))

    _parse(fake, pdf)

    rendered = "\n".join(JsonFormatter().format(record) for record in mineru_log.records)
    assert '"bytes_read"' in rendered
    assert "canary" not in rendered


# --- health ----------------------------------------------------------------------------


def test_ping_is_true_when_health_returns_200() -> None:
    with fake_mineru() as mineru:
        assert asyncio.run(MinerUClient.ping(mineru.url)) is True
    assert mineru.seen == []


def test_ping_is_false_when_health_is_not_ready() -> None:
    with fake_mineru(health=[503]) as mineru:
        assert asyncio.run(MinerUClient.ping(mineru.url)) is False


def test_ping_is_false_when_the_service_is_unreachable() -> None:
    assert asyncio.run(MinerUClient.ping("http://127.0.0.1:9")) is False
