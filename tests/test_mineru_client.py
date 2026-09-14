"""MinerUClient against a misbehaving mineru-api."""

import json
from typing import Any

import httpx2
import pytest

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
