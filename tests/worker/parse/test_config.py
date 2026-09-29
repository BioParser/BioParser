from pathlib import Path

import pytest
from pydantic import ValidationError

from bioparser.worker.parse.config import WorkerSettings


def test_queue_name_is_required(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        WorkerSettings(
            _env_file=None,
            redis_url="redis://localhost:6379/0",
            artifact_storage_path=tmp_path,
        )


def test_empty_redis_url_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        WorkerSettings(
            _env_file=None,
            redis_url="",
            artifact_storage_path=tmp_path,
        )


def test_time_limit_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        WorkerSettings(
            _env_file=None,
            redis_url="redis://localhost:6379/0",
            artifact_storage_path=tmp_path,
            parse_time_limit_ms=0,
        )


def _settings(tmp_path: Path, limit_ms: int) -> WorkerSettings:
    return WorkerSettings(
        _env_file=None,
        redis_url="redis://localhost:6379/0",
        artifact_storage_path=tmp_path,
        parse_queue_name="parse",
        parse_time_limit_ms=limit_ms,
    )


def test_parser_timeout_is_below_the_queue_limit(tmp_path: Path) -> None:
    assert _settings(tmp_path, 1_800_000).parser_timeout_seconds == 1770.0


def test_parser_timeout_keeps_half_of_a_small_limit(tmp_path: Path) -> None:
    assert _settings(tmp_path, 10_000).parser_timeout_seconds == 5.0
