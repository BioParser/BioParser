from pathlib import Path

import pytest
from pydantic import ValidationError

from bioparser.worker.parse.config import WorkerSettings


def test_queue_name_is_required(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        WorkerSettings(
            _env_file=None,  # type: ignore[call-arg]
            redis_url="redis://localhost:6379/0",  # type: ignore[arg-type]  # type: ignore[arg-type]
            artifact_storage_path=tmp_path,
        )


def test_empty_redis_url_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        WorkerSettings(
            _env_file=None,  # type: ignore[call-arg]
            redis_url="",  # type: ignore[arg-type]
            artifact_storage_path=tmp_path,
        )


def test_liveness_port_defaults_to_8081(tmp_path: Path) -> None:
    settings = WorkerSettings(
        _env_file=None,  # type: ignore[call-arg]
        redis_url="redis://localhost:6379/0",  # type: ignore[arg-type]
        artifact_storage_path=tmp_path,
        parse_queue_name="parse",
    )
    assert settings.liveness_port == 8081


@pytest.mark.parametrize("port", [0, 65536])
def test_liveness_port_must_be_a_tcp_port(tmp_path: Path, port: int) -> None:
    with pytest.raises(ValidationError):
        WorkerSettings(
            _env_file=None,  # type: ignore[call-arg]
            redis_url="redis://localhost:6379/0",  # type: ignore[arg-type]
            artifact_storage_path=tmp_path,
            parse_queue_name="parse",
            liveness_port=port,
        )


def test_time_limit_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        WorkerSettings(
            _env_file=None,  # type: ignore[call-arg]
            redis_url="redis://localhost:6379/0",  # type: ignore[arg-type]  # type: ignore[arg-type]
            artifact_storage_path=tmp_path,
            parse_time_limit_seconds=0,
        )


def _settings(tmp_path: Path, limit_s: float) -> WorkerSettings:
    return WorkerSettings(
        _env_file=None,  # type: ignore[call-arg]
        redis_url="redis://localhost:6379/0",  # type: ignore[arg-type]
        artifact_storage_path=tmp_path,
        parse_queue_name="parse",
        parse_time_limit_seconds=limit_s,
    )


def test_parser_timeout_is_below_the_queue_limit(tmp_path: Path) -> None:
    assert _settings(tmp_path, 1800).parser_timeout_seconds == 1770.0


def test_parser_timeout_keeps_half_of_a_small_limit(tmp_path: Path) -> None:
    assert _settings(tmp_path, 10).parser_timeout_seconds == 5.0
