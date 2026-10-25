"""MinerUParser against a mineru-api on loopback."""

import asyncio
import hashlib
import json
import socket
from pathlib import Path
from typing import Any

import pytest
from stubs import STUB_JSON, fake_mineru, file_parse_reply

from bioparser.parser.backend.factory import get_parser
from bioparser.parser.backend.mineru.parser import MinerUParser
from bioparser.parser.errors import (
    ParserBackendUnavailableError,
    ParserProcessError,
    ParserTimeoutError,
    UnsupportedDocumentError,
)
from bioparser.services.mineru import client as client_module

PDF = b"%PDF-1.4\n%%EOF\n"


@pytest.fixture(autouse=True)
def quick_health_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "_READY_POLL_S", 0.01)


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    path = tmp_path / "paper.pdf"
    path.write_bytes(PDF)
    return path


def _failed(code: str) -> tuple[int, dict[str, Any]]:
    entry = {"name": "a.pdf", "status": "failed", "error": {"code": code, "message": "x"}}
    return 409, {"task_id": "job_1", "status": "failed", "files": [entry]}


def test_the_reply_is_mapped_with_the_pdf_checksum_and_settings(pdf: Path) -> None:
    with fake_mineru(file_parse_reply()) as (url, seen):
        artifact = asyncio.run(MinerUParser(url, timeout_s=120.0).parse(pdf))

    checksum = hashlib.sha256(PDF).hexdigest()
    assert artifact.checksum == checksum
    assert (artifact.parser.name, artifact.parser.version) == ("mineru", "2")
    assert artifact.parser.configuration == {"tier": "basic", "ocr_mode": "auto"}
    assert artifact.pages[0].blocks[0].content.type == "text"
    assert seen == [
        "POST /v1/file_parse?tier=basic&ocr_mode=auto&output_formats=middle_json&wait_timeout=120"
    ]


def test_the_tier_is_asked_for_and_recorded(pdf: Path) -> None:
    with fake_mineru(file_parse_reply()) as (url, seen):
        artifact = asyncio.run(MinerUParser(url, tier="standard").parse(pdf))

    assert "tier=standard&" in seen[0]
    assert artifact.parser.version == "2"
    assert artifact.parser.configuration["tier"] == "standard"


def test_an_unknown_tier_from_the_environment_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIOPARSER_MINERU_TIER", "full")

    with pytest.raises(ParserBackendUnavailableError, match="'full'"):
        get_parser("mineru")


def test_without_a_limit_mineru_waits_its_maximum(pdf: Path) -> None:
    with fake_mineru(file_parse_reply()) as (url, seen):
        asyncio.run(MinerUParser(url).parse(pdf))

    assert seen[0].endswith("wait_timeout=3600")


def test_a_restarting_service_is_waited_for(pdf: Path) -> None:
    with fake_mineru(file_parse_reply(), health=[503, 503]) as (url, seen):
        asyncio.run(MinerUParser(url, timeout_s=120.0).parse(pdf))

    assert [request.split("?")[0] for request in seen] == ["POST /v1/file_parse"]


def test_a_failed_parse_is_a_process_error_to_retry(pdf: Path) -> None:
    with (
        fake_mineru(_failed("parse_failed")) as (url, _seen),
        pytest.raises(ParserProcessError, match="parse_failed"),
    ):
        asyncio.run(MinerUParser(url).parse(pdf))


def test_a_parse_past_its_limit_is_a_timeout_and_is_cancelled(pdf: Path) -> None:
    with (
        fake_mineru((202, {"task_id": "job_7", "status": "running"})) as (url, seen),
        pytest.raises(ParserTimeoutError),
    ):
        asyncio.run(MinerUParser(url, timeout_s=5.0).parse(pdf))

    assert seen[-1] == "DELETE /v1/tasks/job_7"


def test_a_parse_still_queued_at_its_limit_is_cancelled_and_retried(pdf: Path) -> None:
    with (
        fake_mineru((202, {"task_id": "job_7", "status": "queued"})) as (url, seen),
        pytest.raises(ParserProcessError, match="did not start"),
    ):
        asyncio.run(MinerUParser(url, timeout_s=5.0).parse(pdf))

    assert seen[-1] == "DELETE /v1/tasks/job_7"


def test_a_tier_the_service_does_not_serve_fails_without_retries(pdf: Path) -> None:
    refused = {"error": {"type": "invalid_request_error", "code": "invalid_request"}}

    with (
        fake_mineru((400, refused)) as (url, _seen),
        pytest.raises(ParserBackendUnavailableError, match="400 \\(invalid_request\\)"),
    ):
        asyncio.run(MinerUParser(url, tier="advanced").parse(pdf))


def test_an_unreachable_service_is_a_process_error(pdf: Path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]  # nothing listens once the socket closes

    with pytest.raises(ParserProcessError, match="not ready"):
        asyncio.run(MinerUParser(f"http://127.0.0.1:{port}", timeout_s=0.1).parse(pdf))


def test_a_proxy_in_the_environment_is_not_used(pdf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")  # discard port: nothing answers
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)

    with fake_mineru(file_parse_reply()) as (url, seen):
        asyncio.run(MinerUParser(url, timeout_s=2.0).parse(pdf))

    assert len(seen) == 1


def test_middle_json_from_another_mineru_fails_without_blaming_the_pdf(pdf: Path) -> None:
    stale = json.loads(json.dumps(STUB_JSON))
    stale["metadata"]["producer"]["version"] = "3.4.5"

    with (
        fake_mineru(file_parse_reply(stale)) as (url, _seen),
        pytest.raises(ParserBackendUnavailableError, match="'3.4.5'"),
    ):
        asyncio.run(MinerUParser(url).parse(pdf))


def test_middle_json_without_text_is_an_unsupported_document(pdf: Path) -> None:
    empty = json.loads(json.dumps(STUB_JSON))
    for page in empty["pages"]:
        page["blocks"] = []

    with (
        fake_mineru(file_parse_reply(empty)) as (url, _seen),
        pytest.raises(UnsupportedDocumentError, match="no mappable"),
    ):
        asyncio.run(MinerUParser(url).parse(pdf))


def test_a_missing_pdf_is_not_sent(tmp_path: Path) -> None:
    with fake_mineru() as (url, seen), pytest.raises(FileNotFoundError):
        asyncio.run(MinerUParser(url).parse(tmp_path / "missing.pdf"))

    assert seen == []
