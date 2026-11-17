"""MinerUParser against a mineru-api on loopback."""

import asyncio
import hashlib
import json
import socket
from pathlib import Path
from typing import Any

import pytest
from stubs import (
    STUB_JSON,
    failed_task,
    fake_mineru,
    parsed_task,
    refused_task,
    unfinished_task,
)

from bioparser.parser.backend.factory import get_parser
from bioparser.parser.backend.mineru import parser as parser_module
from bioparser.parser.backend.mineru.parser import MinerUParser
from bioparser.parser.errors import (
    ParserBackendUnavailableError,
    ParserProcessError,
    ParserTimeoutError,
    UnsupportedDocumentError,
)
from bioparser.services.mineru import client as client_module
from bioparser.services.mineru.client import MAX_WAIT_SECONDS, MinerUClient

PDF = b"%PDF-1.4\n%%EOF\n"


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    path = tmp_path / "paper.pdf"
    path.write_bytes(PDF)
    return path


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "_FIRST_POLL_S", 0.01)
    monkeypatch.setattr(client_module, "_MAX_POLL_S", 0.02)


def test_the_reply_is_mapped_with_the_pdf_checksum_and_settings(pdf: Path) -> None:
    with fake_mineru(parsed_task()) as mineru:
        artifact = asyncio.run(MinerUParser(mineru.url, timeout_s=120.0).parse(pdf))

    checksum = hashlib.sha256(PDF).hexdigest()
    assert artifact.checksum == checksum
    assert (artifact.parser.name, artifact.parser.version) == ("mineru", "3")
    assert artifact.parser.configuration == {"tier": "basic", "ocr_mode": "auto"}
    assert artifact.pages[0].blocks[0].content.type == "text"
    assert mineru.created == [
        {
            "files": [{"file_name": f"{checksum}.pdf", "bytes": len(PDF), "sha256sum": checksum}],
            "tier": "basic",
            "ocr_mode": "auto",
            "output_formats": ["default"],
        }
    ]
    assert mineru.seen == [
        "POST /v1/tasks",
        "PUT /v1/tasks/job_1/uploads/file-job_1",
        "GET /v1/tasks/job_1",
        "GET /v1/files/file-job_1/content",
    ]


def test_the_tier_is_asked_for_and_recorded(pdf: Path) -> None:
    with fake_mineru(parsed_task()) as mineru:
        artifact = asyncio.run(MinerUParser(mineru.url, tier="standard").parse(pdf))

    assert mineru.created[0]["tier"] == "standard"
    assert artifact.parser.version == "3"
    assert artifact.parser.configuration["tier"] == "standard"


def test_an_unknown_tier_from_the_environment_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIOPARSER_MINERU_TIER", "full")

    with pytest.raises(ParserBackendUnavailableError, match="'full'"):
        get_parser("mineru")


def test_without_a_limit_mineru_waits_its_maximum(
    pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []
    real_client = client_module.MinerUClient

    def recording_client(base_url: str, timeout: float, *args: Any, **kwargs: Any) -> MinerUClient:
        waits.append(timeout)
        return real_client(base_url, timeout, *args, **kwargs)

    monkeypatch.setattr(parser_module, "MinerUClient", recording_client)
    with fake_mineru(parsed_task()) as mineru:
        asyncio.run(MinerUParser(mineru.url).parse(pdf))

    assert waits == [MAX_WAIT_SECONDS]


def test_a_failed_parse_is_a_process_error_to_retry(pdf: Path) -> None:
    with (
        fake_mineru(failed_task("parse_failed")) as mineru,
        pytest.raises(ParserProcessError, match="parse_failed"),
    ):
        asyncio.run(MinerUParser(mineru.url).parse(pdf))


@pytest.mark.parametrize("code", ["file_corrupted", "file_encrypted"])
def test_a_pdf_mineru_cannot_read_is_an_unsupported_document(pdf: Path, code: str) -> None:
    # Not retried: the same PDF fails the same way.
    with (
        fake_mineru(failed_task(code)) as mineru,
        pytest.raises(UnsupportedDocumentError, match=code),
    ):
        asyncio.run(MinerUParser(mineru.url).parse(pdf))


def test_a_parse_past_its_limit_is_a_timeout_and_is_cancelled(pdf: Path) -> None:
    with (
        fake_mineru(unfinished_task("running", task_id="job_7")) as mineru,
        pytest.raises(ParserTimeoutError),
    ):
        asyncio.run(MinerUParser(mineru.url, timeout_s=0.3).parse(pdf))

    assert mineru.seen[-1] == "POST /v1/tasks/job_7/cancel"


def test_a_parse_still_queued_at_its_limit_is_cancelled_and_retried(pdf: Path) -> None:
    with (
        fake_mineru(unfinished_task("pending", task_id="job_7")) as mineru,
        pytest.raises(ParserProcessError, match="did not start"),
    ):
        asyncio.run(MinerUParser(mineru.url, timeout_s=0.3).parse(pdf))

    assert mineru.seen[-1] == "POST /v1/tasks/job_7/cancel"


def test_a_tier_the_service_does_not_serve_fails_without_retries(pdf: Path) -> None:
    refused = {"error": {"code": "invalid_request", "message": "x", "param": "tier"}}

    with (
        fake_mineru(refused_task(400, refused)) as mineru,
        pytest.raises(ParserBackendUnavailableError, match="400 \\(invalid_request\\)"),
    ):
        asyncio.run(MinerUParser(mineru.url, tier="advanced").parse(pdf))


def test_an_unreachable_service_is_a_process_error(pdf: Path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]  # nothing listens once the socket closes

    with pytest.raises(ParserProcessError, match="unreachable"):
        asyncio.run(MinerUParser(f"http://127.0.0.1:{port}", timeout_s=0.1).parse(pdf))


def test_a_proxy_in_the_environment_is_not_used(pdf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")  # discard port: nothing answers
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)

    with fake_mineru(parsed_task()) as mineru:
        asyncio.run(MinerUParser(mineru.url, timeout_s=2.0).parse(pdf))

    assert len(mineru.seen) == 4


def test_middle_json_from_another_mineru_fails_without_blaming_the_pdf(pdf: Path) -> None:
    stale = json.loads(json.dumps(STUB_JSON))
    stale["metadata"]["producer"]["version"] = "4.0.12"

    with (
        fake_mineru(parsed_task(stale)) as mineru,
        pytest.raises(ParserBackendUnavailableError, match="'4.0.12'"),
    ):
        asyncio.run(MinerUParser(mineru.url).parse(pdf))


def test_middle_json_without_text_is_an_unsupported_document(pdf: Path) -> None:
    empty = json.loads(json.dumps(STUB_JSON))
    for page in empty["pages"]:
        page["blocks"] = []

    with (
        fake_mineru(parsed_task(empty)) as mineru,
        pytest.raises(UnsupportedDocumentError, match="no mappable"),
    ):
        asyncio.run(MinerUParser(mineru.url).parse(pdf))


def test_a_missing_pdf_is_not_sent(tmp_path: Path) -> None:
    with fake_mineru() as mineru, pytest.raises(FileNotFoundError):
        asyncio.run(MinerUParser(mineru.url).parse(tmp_path / "missing.pdf"))

    assert mineru.seen == []
