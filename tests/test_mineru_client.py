"""MinerUClient and SyncMinerUClient against a misbehaving mineru-api."""

import asyncio
import itertools
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx2
import pytest

from bioparser import logging_config
from bioparser.logging_config import JsonFormatter
from bioparser.services.mineru import client as client_module
from bioparser.services.mineru.client import (
    MAX_RESPONSE_BYTES,
    MinerUClient,
    MinerUError,
    MinerUPermanentError,
    MinerUTier,
    MinerUTimeoutError,
    SyncMinerUClient,
)

PDF = b"%PDF-1.4\n%%EOF\n"
MIDDLE = {"schema": "docvortex.middle", "pages": []}

type Handler = Callable[[httpx2.Request], httpx2.Response]


def _result(middle: object = MIDDLE, **file: object) -> bytes:
    """A /v1/file_parse JSON body for one file."""
    entry = {"name": "a.pdf", "status": "completed", "content": {"middle_json": middle}} | file
    return json.dumps({"task_id": "job_1", "status": "completed", "files": [entry]}).encode()


def _client(handler: object, tier: MinerUTier = "basic") -> MinerUClient:
    client = MinerUClient("http://mineru:8000", 5.0, 1.0, tier=tier)
    client._http = httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),  # type: ignore[arg-type]
        base_url="http://mineru:8000",
    )
    return client


def _sync_client(handler: Handler, tier: MinerUTier = "basic") -> SyncMinerUClient:
    client = SyncMinerUClient("http://mineru:8000", wait_s=5.0, tier=tier)
    client._http.close()
    client._http = httpx2.Client(
        transport=httpx2.MockTransport(handler), base_url="http://mineru:8000"
    )
    return client


def _parse(
    kind: str, handler: Handler, pdf: bytes = PDF, tier: MinerUTier = "basic"
) -> dict[str, Any]:
    """Run one parse through the async or the sync client."""
    if kind == "sync":
        with _sync_client(handler, tier) as client:
            return client.parse(filename="a.pdf", content=pdf)

    async def run() -> dict[str, Any]:
        client = _client(handler, tier)
        try:
            return await client.parse(filename="a.pdf", content=pdf)
        finally:
            await client.aclose()

    return asyncio.run(run())


both_clients = pytest.mark.parametrize("kind", ["async", "sync"])


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@both_clients
def test_the_request_asks_for_basic_middle_json_with_only_a_file_field(kind: str) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, content=_result())

    assert _parse(kind, handler) == MIDDLE
    (request,) = seen
    assert (request.method, request.url.path) == ("POST", "/v1/file_parse")
    assert dict(request.url.params) == {
        "tier": "basic",
        "ocr_mode": "auto",
        "output_formats": "middle_json",
        "wait_timeout": "5",
    }
    # mineru-api allows no form fields besides the files.
    body = request.read()
    assert body.count(b"Content-Disposition") == 1
    assert b'name="files"; filename="a.pdf"' in body


@both_clients
def test_the_tier_is_the_one_the_client_was_given(kind: str) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, content=_result())

    _parse(kind, handler, tier="advanced")

    assert seen[0].url.params["tier"] == "advanced"


@pytest.mark.anyio
async def test_neither_client_sends_through_a_proxy_from_the_environment() -> None:
    # The PDF goes straight to the internal service, whatever HTTP_PROXY says.
    client = MinerUClient("http://mineru:8000", 5.0, 1.0)
    with SyncMinerUClient("http://mineru:8000", wait_s=5.0) as sync_client:
        assert not sync_client._http.trust_env
    assert not client._http.trust_env
    await client.aclose()


@both_clients
def test_an_oversized_body_is_refused_not_buffered(kind: str) -> None:
    oversized = _result(middle="x" * (MAX_RESPONSE_BYTES + 1))

    # The same PDF gives the same result, so a retry would parse it again for nothing.
    with pytest.raises(MinerUPermanentError, match="exceeded"):
        _parse(kind, lambda _request: httpx2.Response(200, content=oversized))


def _v1_error(code: str) -> bytes:
    return json.dumps(
        {"error": {"type": "invalid_request_error", "code": code, "message": "secret text"}}
    ).encode()


@both_clients
@pytest.mark.parametrize(
    ("status", "body", "error", "message"),
    [
        (400, _v1_error("invalid_request"), MinerUPermanentError, "400 \\(invalid_request\\)"),
        (413, _v1_error("file_too_large"), MinerUPermanentError, "413 \\(file_too_large\\)"),
        (429, _v1_error("rate_limited"), MinerUError, "429 \\(rate_limited\\)"),
        (503, b"upstream down", MinerUError, "503 \\(unknown\\)"),
    ],
    ids=["tier-not-served", "too-large", "busy", "down"],
)
def test_a_status_without_a_parse_is_judged_by_status_and_code(
    kind: str, status: int, body: bytes, error: type[MinerUError], message: str
) -> None:
    with pytest.raises(MinerUError, match=message) as caught:
        _parse(kind, lambda _request: httpx2.Response(status, content=body))
    assert type(caught.value) is error
    assert "secret text" not in str(caught.value)


@both_clients
def test_a_large_error_body_is_not_read_to_the_result_cap(kind: str) -> None:
    # Classified by its status, like any 503, not refused as an oversized result.
    with pytest.raises(MinerUError, match="503 \\(unknown\\)") as caught:
        _parse(kind, lambda _request: httpx2.Response(503, content=b"x" * (MAX_RESPONSE_BYTES + 1)))
    assert type(caught.value) is MinerUError


@both_clients
def test_a_parse_still_running_after_the_wait_is_cancelled(kind: str) -> None:
    deleted: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "DELETE":
            deleted.append(request.url.raw_path.decode())
            return httpx2.Response(200, json={})
        return httpx2.Response(202, json={"task_id": "job/../x", "status": "running"})

    with pytest.raises(MinerUTimeoutError):
        _parse(kind, handler)
    assert deleted == ["/v1/tasks/job%2F..%2Fx"]


@both_clients
def test_a_parse_that_never_left_the_queue_is_cancelled_and_retryable(kind: str) -> None:
    deleted: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "DELETE":
            deleted.append(request.url.path)
            return httpx2.Response(200, json={})
        return httpx2.Response(202, json={"task_id": "job_1", "status": "queued"})

    with pytest.raises(MinerUError, match="did not start") as caught:
        _parse(kind, handler)
    assert type(caught.value) is MinerUError
    assert deleted == ["/v1/tasks/job_1"]


@both_clients
def test_a_failed_cancel_still_reports_the_timeout(kind: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "DELETE":
            raise httpx2.ConnectError("gone", request=request)
        return httpx2.Response(202, json={"task_id": "job_1"})

    with pytest.raises(MinerUTimeoutError):
        _parse(kind, handler)


@both_clients
@pytest.mark.parametrize(
    ("code", "message"),
    [("parse_failed", "parse failed \\(parse_failed\\)"), ("Not a code\n", "unknown")],
    ids=["catch-all", "garbage"],
)
def test_a_failed_parse_is_retryable_and_keeps_only_its_code(
    kind: str, code: str, message: str
) -> None:
    body = _result(status="failed", content=None, error={"code": code, "message": "secret text"})

    with pytest.raises(MinerUError, match=message) as caught:
        _parse(kind, lambda _request: httpx2.Response(409, content=body))
    assert type(caught.value) is MinerUError
    assert "secret text" not in str(caught.value)


@both_clients
@pytest.mark.parametrize(
    "exc",
    [httpx2.ReadTimeout("slow"), httpx2.ConnectTimeout("no route"), httpx2.ConnectError("refused")],
    ids=["read-timeout", "connect-timeout", "refused"],
)
def test_transport_failures_are_retryable(kind: str, exc: httpx2.HTTPError) -> None:
    # The read timeout outlasts the server's wait: running out of it means no answer came.
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise exc

    with pytest.raises(MinerUError, match="unreachable") as caught:
        _parse(kind, handler)
    assert type(caught.value) is MinerUError


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'"not an object"',
        b'["not", "an", "object"]',
        b"{}",
        b'{"files": []}',
        b'{"files": [{}, {}]}',
        b'{"files": ["not an object"]}',
        _result(content={}),
        _result(middle="{}"),
        _result(middle={}),
    ],
    ids=[
        "not-json",
        "body-str",
        "body-list",
        "no-files",
        "zero-files",
        "two-files",
        "entry-str",
        "no-middle-json",
        "middle-json-str",
        "middle-json-empty",
    ],
)
@both_clients
def test_malformed_bodies_are_retryable_errors(kind: str, body: bytes) -> None:
    with pytest.raises(MinerUError) as caught:
        _parse(kind, lambda _request: httpx2.Response(200, content=body))
    assert type(caught.value) is MinerUError


# --- logging ---------------------------------------------------------------

MIB = 1024 * 1024
EXCHANGE_FIELDS = frozenset({"status_code", "bytes_read", "latency_ms", "error"})


@pytest.fixture
def mineru_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> pytest.LogCaptureFixture:
    """Capture the client's records, with a clock that makes every request take 250 ms."""
    caplog.set_level(logging.INFO, logger=client_module.__name__)
    monkeypatch.setattr(logging_config, "perf_counter", itertools.count(100.0, 0.25).__next__)
    return caplog


def _exchanges(caplog: pytest.LogCaptureFixture) -> list[tuple[int, dict[str, object]]]:
    """(level, fields) of every record the client logged."""
    return [
        (record.levelno, {k: v for k, v in vars(record).items() if k in EXCHANGE_FIELDS})
        for record in caplog.records
        if record.name == client_module.__name__
    ]


@both_clients
def test_a_completed_request_logs_status_bytes_and_latency(
    kind: str, mineru_log: pytest.LogCaptureFixture
) -> None:
    body = _result()

    _parse(kind, lambda _request: httpx2.Response(200, content=body))

    assert _exchanges(mineru_log) == [
        (logging.INFO, {"status_code": 200, "bytes_read": len(body), "latency_ms": 250.0})
    ]


def _upstream_down(_request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(503, content=b"upstream down")


async def _mib_chunks(count: int) -> AsyncIterator[bytes]:
    for _ in range(count):
        yield b"x" * MIB


def _twice_the_cap(_request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, content=_mib_chunks(2 * MAX_RESPONSE_BYTES // MIB))


def _refused(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("connection refused", request=request)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("handler", "fields"),
    [
        (_upstream_down, {"status_code": 503, "bytes_read": 13, "error": "MinerUError"}),
        # Abandoned at the first chunk over the cap, not after reading all 64 MiB
        (
            _twice_the_cap,
            {
                "status_code": 200,
                "bytes_read": MAX_RESPONSE_BYTES + MIB,
                "error": "MinerUPermanentError",
            },
        ),
        (_refused, {"status_code": None, "bytes_read": 0, "error": "ConnectError"}),
    ],
    ids=["non-200", "oversized", "unreachable"],
)
async def test_a_failed_request_logs_one_warning(
    mineru_log: pytest.LogCaptureFixture, handler: object, fields: dict[str, object]
) -> None:
    client = _client(handler)

    with pytest.raises(MinerUError):
        await client.parse(filename="a.pdf", content=PDF)
    await client.aclose()

    assert _exchanges(mineru_log) == [(logging.WARNING, {**fields, "latency_ms": 250.0})]


@pytest.mark.anyio
async def test_a_cancelled_request_is_logged_and_stays_cancelled(
    mineru_log: pytest.LogCaptureFixture,
) -> None:
    async def never_answers(_request: httpx2.Request) -> httpx2.Response:
        await asyncio.Event().wait()  # until cancelled
        return httpx2.Response(200)

    client = _client(never_answers)

    # TimeoutError means the CancelledError propagated; a swallowed one would not time out
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await client.parse(filename="a.pdf", content=PDF)
    await client.aclose()

    assert _exchanges(mineru_log) == [
        (
            logging.WARNING,
            {"status_code": None, "bytes_read": 0, "latency_ms": 250.0, "error": "CancelledError"},
        )
    ]


@both_clients
def test_neither_the_pdf_nor_the_response_body_is_logged(
    kind: str, mineru_log: pytest.LogCaptureFixture
) -> None:
    pdf = b"%PDF-1.4\n% canary-in-the-pdf\n%%EOF\n"
    body = _result(middle={"note": "canary-in-the-body"})

    _parse(kind, lambda _request: httpx2.Response(200, content=body), pdf)

    rendered = "\n".join(JsonFormatter().format(record) for record in mineru_log.records)
    assert '"bytes_read"' in rendered
    assert "canary" not in rendered
