"""The API and both workers read their passwords from Docker secrets and never show them."""

from pathlib import Path

import pytest

from bioparser.api.config import ApiSettings
from bioparser.worker.extract.config import ExtractWorkerSettings
from bioparser.worker.parse.config import WorkerSettings

REDIS = "canary-redis-secret-31d9"
DATABASE = "canary-db-secret-7a2e"
URL_PASSWORD = "canary-url-pass"


@pytest.fixture
def secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The files that Compose mounts in /run/secrets. No developer .env."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BIOPARSER_ARTIFACT_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("BIOPARSER_PARSE_QUEUE_NAME", "parse")
    monkeypatch.setenv("BIOPARSER_EXTRACT_QUEUE_NAME", "extract")
    # A password in a URL, as in the Kubernetes manifests, must stay hidden too.
    monkeypatch.setenv("BIOPARSER_REDIS_URL", f"redis://:{URL_PASSWORD}@redis:6379/0")
    monkeypatch.setenv("BIOPARSER_DATABASE_URL", "postgresql://bioparser_app@postgres/bioparser")
    directory = tmp_path / "run-secrets"
    directory.mkdir()
    (directory / "bioparser_redis_password").write_text(f"{REDIS}\n", encoding="utf-8")
    (directory / "bioparser_database_password").write_text(f"{DATABASE}\n", encoding="utf-8")
    return directory


@pytest.mark.parametrize("settings_class", [ApiSettings, WorkerSettings, ExtractWorkerSettings])
def test_the_passwords_come_from_docker_secrets_and_never_show(
    settings_class: type[ApiSettings] | type[WorkerSettings] | type[ExtractWorkerSettings],
    secrets_dir: Path,
) -> None:
    settings = settings_class(_secrets_dir=secrets_dir)  # type: ignore[call-arg]

    assert settings.redis_password is not None
    assert settings.redis_password.get_secret_value() == REDIS
    assert settings.database_password is not None
    assert settings.database_password.get_secret_value() == DATABASE
    shown = [repr(settings), str(settings), str(settings.model_dump()), settings.model_dump_json()]
    for output in shown:
        for secret in (REDIS, DATABASE, URL_PASSWORD):
            assert secret not in output


def test_the_api_key_comes_from_its_docker_secret(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "canary-api-key-from-a-docker-secret-0123"
    (secrets_dir / "bioparser_api_key").write_text(key, encoding="utf-8")
    monkeypatch.delenv("BIOPARSER_API_KEY")  # a variable has priority over the file

    settings = ApiSettings(_secrets_dir=secrets_dir)  # type: ignore[call-arg]

    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == key
