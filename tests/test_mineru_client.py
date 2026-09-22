"""MinerUClient against a misbehaving mineru-api."""

import asyncio
import itertools
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest

from bioparser.logging_config import JsonFormatter
from bioparser.services.mineru import client as client_module
from bioparser.services.mineru.client import (
    MAX_RESPONSE_BYTES,
    MinerUClient,
    MinerUError,
)

PDF = b"%PDF-1.4\n%%EOF\n"


def _client(handler: object) -> MinerUClient:
    client = MinerUClient("http://mineru:8000", 5.0, 1.0)
    client._http = httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),  # type: ignore[arg-type]
        base_url="http://mineru:8000",
    )
    return client


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_an_oversized_body_is_refused_not_buffered() -> None:
    oversized = b'{"results":{"a":{"middle_json":"' + b"x" * (MAX_RESPONSE_BYTES + 1) + b'"}}}'
    client = _client(lambda _request: httpx2.Response(200, content=oversized))

    with pytest.raises(MinerUError, match="exceeded"):
        await client.parse(filename="a.pdf", content=PDF)

    await client.aclose()


@pytest.mark.anyio
async def test_a_body_under_the_cap_still_parses() -> None:
    middle = {"_backend": "pipeline", "_version_name": "3.4.5", "pdf_info": []}
    body = json.dumps({"results": {"a": {"middle_json": json.dumps(middle)}}}).encode()
    client = _client(lambda _request: httpx2.Response(200, content=body))

    assert await client.parse(filename="a.pdf", content=PDF) == middle

    await client.aclose()


@pytest.mark.anyio
async def test_a_non_200_never_reads_the_body() -> None:
    client = _client(lambda _request: httpx2.Response(503, content=b"upstream down"))

    with pytest.raises(MinerUError, match="503"):
        await client.parse(filename="a.pdf", content=PDF)

    await client.aclose()


@pytest.mark.parametrize(
    "results",
    [["this", "is", "a list"], "a string", 1337, None, {}],
    ids=["list", "str", "int", "null", "empty"],
)
def test_non_dict_results_raise_mineru_error(results: Any) -> None:
    with pytest.raises(MinerUError):
        MinerUClient._middle_json_from_body({"results": results})


@pytest.mark.parametrize(
    "body",
    [
        "not an object",
        ["not", "an", "object"],
        {"results": {"doc": "not an object"}},
        {"results": {"doc": {}}},
        {"results": {"doc": {"middle_json": 12345}}},
        {"results": {"doc": {"middle_json": "{not json"}}},
    ],
    ids=["body-str", "body-list", "entry-str", "no-middle-json", "middle-json-int", "bad-json"],
)
def test_malformed_bodies_all_raise_mineru_error(body: Any) -> None:
    with pytest.raises(MinerUError):
        MinerUClient._middle_json_from_body(body)


def test_well_formed_body_is_returned() -> None:
    parsed = MinerUClient._middle_json_from_body(
        {"results": {"doc": {"middle_json": '{"pdf_info": []}'}}}
    )

    assert parsed == {"pdf_info": []}


# --- logging ---------------------------------------------------------------

MIB = 1024 * 1024
EXCHANGE_FIELDS = frozenset({"status_code", "bytes_read", "latency_ms", "error"})


@pytest.fixture
def mineru_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> pytest.LogCaptureFixture:
    """Capture the client's records, with a clock that makes every request take 250 ms."""
    caplog.set_level(logging.INFO, logger=client_module.__name__)
    monkeypatch.setattr(client_module, "perf_counter", itertools.count(100.0, 0.25).__next__)
    return caplog


def _exchanges(caplog: pytest.LogCaptureFixture) -> list[tuple[int, dict[str, object]]]:
    """(level, fields) of every record the client logged."""
    return [
        (record.levelno, {k: v for k, v in vars(record).items() if k in EXCHANGE_FIELDS})
        for record in caplog.records
        if record.name == client_module.__name__
    ]


@pytest.mark.anyio
async def test_a_completed_request_logs_status_bytes_and_latency(
    mineru_log: pytest.LogCaptureFixture,
) -> None:
    body = json.dumps({"results": {"a": {"middle_json": '{"pdf_info": []}'}}}).encode()
    client = _client(lambda _request: httpx2.Response(200, content=body))

    await client.parse(filename="a.pdf", content=PDF)
    await client.aclose()

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
        (_upstream_down, {"status_code": 503, "bytes_read": 0, "error": "MinerUError"}),
        # Abandoned at the first chunk over the cap, not after reading all 64 MiB
        (
            _twice_the_cap,
            {"status_code": 200, "bytes_read": MAX_RESPONSE_BYTES + MIB, "error": "MinerUError"},
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


@pytest.mark.anyio
async def test_neither_the_pdf_nor_the_response_body_is_logged(
    mineru_log: pytest.LogCaptureFixture,
) -> None:
    pdf = b"%PDF-1.4\n% canary-in-the-pdf\n%%EOF\n"
    middle = json.dumps({"pdf_info": [], "note": "canary-in-the-body"})
    body = json.dumps({"results": {"a": {"middle_json": middle}}}).encode()
    client = _client(lambda _request: httpx2.Response(200, content=body))

    await client.parse(filename="a.pdf", content=pdf)
    await client.aclose()

    rendered = "\n".join(JsonFormatter().format(record) for record in mineru_log.records)
    assert '"bytes_read"' in rendered
    assert "canary" not in rendered
