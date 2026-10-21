from pathlib import Path

import pytest
from pydantic import ValidationError

from bioparser.worker.extract.config import ExtractWorkerSettings


def test_redis_url_and_storage_path_are_required(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ExtractWorkerSettings(_env_file=None)  # type: ignore[call-arg]


def test_claim_cap_follows_the_retry_count(tmp_path: Path) -> None:
    settings = ExtractWorkerSettings(
        _env_file=None,  # type: ignore[call-arg]
        redis_url="redis://localhost:6379/0",  # type: ignore[arg-type]
        artifact_storage_path=tmp_path,
        extract_queue_name="extract",
        extract_max_retries=5,
    )
    assert settings.extract_max_claims == 6
    assert settings.extraction_char_budget == 8000
    assert settings.extraction_max_tokens == 1024
    assert settings.extract_queue_name == "extract"
    assert settings.liveness_port == 8081


def test_extract_queue_name_is_required(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ExtractWorkerSettings(
            _env_file=None,  # type: ignore[call-arg]
            redis_url="redis://localhost:6379/0",  # type: ignore[arg-type]
            artifact_storage_path=tmp_path,
        )


@pytest.mark.parametrize("port", [0, 65536])
def test_liveness_port_must_be_a_tcp_port(tmp_path: Path, port: int) -> None:
    with pytest.raises(ValidationError):
        ExtractWorkerSettings(
            _env_file=None,  # type: ignore[call-arg]
            redis_url="redis://localhost:6379/0",  # type: ignore[arg-type]
            artifact_storage_path=tmp_path,
            extract_queue_name="extract",
            liveness_port=port,
        )


def test_empty_redis_url_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ExtractWorkerSettings(
            _env_file=None,  # type: ignore[call-arg]
            redis_url="",  # type: ignore[arg-type]
            artifact_storage_path=tmp_path,
        )
