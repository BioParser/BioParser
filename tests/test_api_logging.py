"""Logging in the API layer at startup."""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from bioparser.api import config
from bioparser.api.app import app
from bioparser.logging_config import JsonFormatter


def _rendered(records: list[logging.LogRecord]) -> str:
    return "\n".join(JsonFormatter().format(record) for record in records)


def _app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path | None = None, *, redis_url: str
) -> TestClient:
    """TestClient whose lifespan-built clients are dropped again after the test.

    This sets the Redis URL, the database URL and the storage path, then reads the
    settings. get_api_settings() caches them, so set other env vars before the call.
    """
    monkeypatch.setenv("BIOPARSER_REDIS_URL", redis_url)
    monkeypatch.setenv("BIOPARSER_DATABASE_URL", "postgresql://bioparser@127.0.0.1:1/bioparser")
    monkeypatch.setenv(
        "BIOPARSER_ARTIFACT_STORAGE_PATH",
        str((tmp_path or Path("/tmp")) / "bioparser-test-artifacts"),
    )
    for name in ("parse_queue", "artifact_storage"):
        monkeypatch.setattr(app.state, name, None, raising=False)
    api_key = config.get_api_settings().api_key
    headers = {"X-API-Key": api_key.get_secret_value()} if api_key else {}
    return TestClient(app, headers=headers)


def test_the_redis_password_never_appears_in_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    password = "canary-redis-pass"
    monkeypatch.setenv("BIOPARSER_LOG_LEVEL", "DEBUG")
    caplog.set_level(logging.DEBUG)

    # Port 1 refuses the connection, also when a local Redis listens on 6379.
    with _app(monkeypatch, redis_url=f"redis://:{password}@127.0.0.1:1/0") as client:
        response = client.get("/ready")

    # The app read this URL, so the checks below have a password to find.
    assert config.get_api_settings().redis_url.password == password
    assert response.status_code == 503
    assert "redis" in response.json()["detail"]["unavailable"]
    assert password not in _rendered(caplog.records) + capsys.readouterr().err
    assert password not in repr(config.get_api_settings())


def test_a_malformed_database_url_does_not_echo_its_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "BIOPARSER_DATABASE_URL", "postgresql://bioparser:canary-db-pass@postgres:99999/bioparser"
    )

    with pytest.raises(ValidationError, match="database_url") as failure:
        config.ApiSettings()  # type: ignore[call-arg]

    assert "canary-db-pass" not in str(failure.value)


def test_a_malformed_redis_url_does_not_echo_its_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # app.py loads the settings at import, so this error's traceback reaches the
    # container log as plain text, after the JSON record app.py logs about it.
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://:canary-redis-pass@redis:99999/0")

    with pytest.raises(ValidationError, match="redis_url") as failure:
        config.ApiSettings()  # type: ignore[call-arg]

    assert "canary-redis-pass" not in str(failure.value)
