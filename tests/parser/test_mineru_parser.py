"""MinerUParser against a mineru-api on loopback."""

import hashlib
import socket
from pathlib import Path
from typing import Any

import pytest
from stubs import fake_mineru, file_parse_reply

from bioparser.parser.backend.mineru.parser import MinerUParser
from bioparser.parser.errors import (
    ParserProcessError,
    ParserTimeoutError,
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
        artifact = MinerUParser(url, timeout_s=120.0).parse(pdf)

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
        artifact = MinerUParser(url, tier="standard").parse(pdf)

    assert "tier=standard&" in seen[0]
    assert artifact.parser.version == "2"
    assert artifact.parser.configuration["tier"] == "standard"


def test_a_restarting_service_is_waited_for(pdf: Path) -> None:
    with fake_mineru(file_parse_reply(), health=[503, 503]) as (url, seen):
        MinerUParser(url, timeout_s=120.0).parse(pdf)

    assert [request.split("?")[0] for request in seen] == ["POST /v1/file_parse"]


def test_a_failed_parse_is_a_process_error_to_retry(pdf: Path) -> None:
    with (
        fake_mineru(_failed("parse_failed")) as (url, _seen),
        pytest.raises(ParserProcessError, match="parse_failed"),
    ):
        MinerUParser(url).parse(pdf)


def test_a_parse_past_its_limit_is_a_timeout_and_is_cancelled(pdf: Path) -> None:
    with (
        fake_mineru((202, {"task_id": "job_7", "status": "running"})) as (url, seen),
        pytest.raises(ParserTimeoutError),
    ):
        MinerUParser(url, timeout_s=5.0).parse(pdf)

    assert seen[-1] == "DELETE /v1/tasks/job_7"


def test_a_parse_still_queued_at_its_limit_is_cancelled_and_retried(pdf: Path) -> None:
    with (
        fake_mineru((202, {"task_id": "job_7", "status": "queued"})) as (url, seen),
        pytest.raises(ParserProcessError, match="did not start"),
    ):
        MinerUParser(url, timeout_s=5.0).parse(pdf)

    assert seen[-1] == "DELETE /v1/tasks/job_7"


def test_an_unreachable_service_is_a_process_error(pdf: Path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]  # nothing listens once the socket closes

    with pytest.raises(ParserProcessError, match="not ready"):
        MinerUParser(f"http://127.0.0.1:{port}", timeout_s=0.1).parse(pdf)


def test_a_missing_pdf_is_not_sent(tmp_path: Path) -> None:
    with fake_mineru() as (url, seen), pytest.raises(FileNotFoundError):
        MinerUParser(url).parse(tmp_path / "missing.pdf")

    assert seen == []
