import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from bioparser.api import config
from bioparser.api.schema import ReadinessResponse
from bioparser.db.engine import DatabaseError, DatabaseUnavailableError

app_module = import_module("bioparser.api.app")


@pytest.fixture(autouse=True)
def postgres_connection(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> AsyncMock:
    """Keep readiness tests independent of local Redis, Postgres, and storage."""
    connection = AsyncMock()

    @asynccontextmanager
    async def transaction() -> AsyncIterator[AsyncMock]:
        yield connection

    class FakeSocket:
        def close(self) -> None:
            pass

    def connect(addr: tuple[str, int], timeout: float | None = None) -> FakeSocket:
        return FakeSocket()

    monkeypatch.setenv("BIOPARSER_ARTIFACT_STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(app_module.socket, "create_connection", connect)
    monkeypatch.setattr(app_module.database, "transaction", transaction)
    return connection


def test_ready_empty_redis_url(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "")
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["unavailable"] == ["config"]


def test_ready_redis_available(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BIOPARSER_ARTIFACT_STORAGE_PATH", str(tmp_path))

    class FakeSocket:
        def close(self) -> None:
            pass

    def fake_create_connection(addr: tuple[str, int], timeout: float | None = None) -> FakeSocket:
        return FakeSocket()

    monkeypatch.setattr(app_module.socket, "create_connection", fake_create_connection)

    response = client.get("/ready")
    assert response.status_code == 200
    ReadinessResponse.model_validate(response.json())
    assert response.json() == {"status": "ready"}


def test_ready_redis_unavailable(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://localhost:65000/0")

    def fake_create_connection(addr: tuple[str, int], timeout: float | None = None) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(app_module.socket, "create_connection", fake_create_connection)

    response = client.get("/ready")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["status"] == "not ready"
    assert "redis" in detail["unavailable"]


def test_ready_storage_unavailable(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    bad = tmp_path / "nope"
    monkeypatch.setenv("BIOPARSER_ARTIFACT_STORAGE_PATH", str(bad))
    response = client.get("/ready")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "storage" in detail["unavailable"]


def test_ready_invalid_timeout(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setenv("BIOPARSER_DEPENDENCY_CHECK_TIMEOUT_SECONDS", "not-a-number")
    response = client.get("/ready")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "config" in detail["unavailable"]


def test_ready_negative_timeout(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setenv("BIOPARSER_DEPENDENCY_CHECK_TIMEOUT_SECONDS", "-1.0")
    response = client.get("/ready")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "config" in detail["unavailable"]


def test_ready_invalid_redis_url(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "not-a-url")
    response = client.get("/ready")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "config" in detail["unavailable"]


def test_ready_empty_database_url(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setenv("BIOPARSER_DATABASE_URL", "")
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["unavailable"] == ["config"]


def test_ready_postgres_available(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, postgres_connection: AsyncMock
) -> None:
    seen: list[tuple[str, int]] = []

    class FakeSocket:
        def close(self) -> None:
            pass

    def connect(addr: tuple[str, int], timeout: float | None = None) -> FakeSocket:
        seen.append(addr)
        return FakeSocket()

    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(app_module.socket, "create_connection", connect)
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    postgres_connection.execute.assert_awaited_once()
    assert str(postgres_connection.execute.call_args.args[0]) == "SELECT 1"
    # Only Redis uses a raw TCP check.
    assert seen == [("localhost", 6379)]


@pytest.mark.parametrize("phase", ["connect", "query"])
@pytest.mark.parametrize("error_type", [DatabaseError, DatabaseUnavailableError])
def test_ready_postgres_unavailable_hides_the_url(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    postgres_connection: AsyncMock,
    phase: str,
    error_type: type[DatabaseError],
) -> None:
    url = "postgresql://bioparser:canary-db-pass@127.0.0.1:5432/bioparser"
    monkeypatch.setenv("BIOPARSER_DATABASE_URL", url)
    error = error_type(f"Failed: {url}")
    if phase == "query":
        postgres_connection.execute.side_effect = error
    else:

        @asynccontextmanager
        async def transaction() -> AsyncIterator[AsyncMock]:
            raise error
            yield postgres_connection

        monkeypatch.setattr(app_module.database, "transaction", transaction)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == {"status": "not ready", "unavailable": ["postgres"]}
    assert "canary-db-pass" not in response.text
    assert "postgresql" not in response.text
    assert "127.0.0.1" not in response.text
    assert "canary-db-pass" not in repr(config.get_api_settings())


@pytest.mark.parametrize("phase", ["connect", "query"])
def test_ready_postgres_timeout_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    postgres_connection: AsyncMock,
    phase: str,
) -> None:
    monkeypatch.setenv("BIOPARSER_DEPENDENCY_CHECK_TIMEOUT_SECONDS", "0.05")
    cancelled = False

    async def hang(*args: object) -> None:
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    if phase == "query":
        postgres_connection.execute.side_effect = hang
    else:

        @asynccontextmanager
        async def transaction() -> AsyncIterator[AsyncMock]:
            await hang()
            yield postgres_connection

        monkeypatch.setattr(app_module.database, "transaction", transaction)

    # A broken timeout fails the test instead of hanging indefinitely.
    async def probe() -> None:
        await asyncio.wait_for(app_module.readiness(), timeout=1.0)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(probe())
    assert caught.value.status_code == 503
    assert cast(object, caught.value.detail) == {
        "status": "not ready",
        "unavailable": ["postgres"],
    }
    assert cancelled


@pytest.mark.parametrize("failed", [(), ("postgres",), ("redis", "postgres", "storage")])
def test_ready_checks_run_together_and_report_failures(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, failed: tuple[str, ...]
) -> None:
    started: set[str] = set()
    all_started = asyncio.Event()

    async def check(name: str) -> bool:
        started.add(name)
        if len(started) == 3:
            all_started.set()
        # No check can complete until all three have started.
        await asyncio.wait_for(all_started.wait(), timeout=1.0)
        return name not in failed

    async def tcp(host: str, port: int, timeout: float) -> bool:
        return await check("redis")

    async def postgres(timeout: float) -> bool:
        return await check("postgres")

    async def storage(path: str, timeout: float) -> bool:
        return await check("storage")

    monkeypatch.setattr(app_module, "_check_tcp", tcp)
    monkeypatch.setattr(app_module, "_check_postgres", postgres)
    monkeypatch.setattr(app_module, "_check_storage", storage)
    response = client.get("/ready")

    if failed:
        assert response.status_code == 503
        assert response.json()["detail"] == {"status": "not ready", "unavailable": list(failed)}
    else:
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}


def test_ready_non_postgres_database_url_is_config(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setenv("BIOPARSER_DATABASE_URL", "redis://:canary-db-pass@localhost:6379/0")
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["unavailable"] == ["config"]
    assert "canary-db-pass" not in response.text


def test_ready_invalid_database_url_hides_the_password(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setenv(
        "BIOPARSER_DATABASE_URL", "postgresql://bioparser:canary-db-pass@postgres:99999/bioparser"
    )
    response = client.get("/ready")
    assert response.status_code == 503
    assert "config" in response.json()["detail"]["unavailable"]
    assert "canary-db-pass" not in response.text
