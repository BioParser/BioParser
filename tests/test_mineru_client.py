"""MinerUClient against a misbehaving mineru-api."""

import json

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
