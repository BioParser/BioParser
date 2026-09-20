from importlib import import_module
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bioparser.api.schema import ReadinessResponse

app_module = import_module("bioparser.api.app")


def test_ready_no_deps(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.delenv("BIOPARSER_REDIS_URL", raising=False)
    monkeypatch.delenv("BIOPARSER_ARTIFACT_STORAGE_PATH", raising=False)
    monkeypatch.delenv("BIOPARSER_DEPENDENCY_CHECK_TIMEOUT_SECONDS", raising=False)
    response = client.get("/ready")
    assert response.status_code == 200
    ReadinessResponse.model_validate(response.json())
    assert response.json() == {"status": "ready"}


def test_ready_empty_redis_url(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "")
    response = client.get("/ready")
    assert response.status_code == 200
    ReadinessResponse.model_validate(response.json())
    assert response.json() == {"status": "ready"}


def test_ready_redis_available(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://localhost:6379/0")

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


def test_ready_storage_available(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    writable_dir = tmp_path / "writable"
    writable_dir.mkdir()
    monkeypatch.setenv("BIOPARSER_ARTIFACT_STORAGE_PATH", str(writable_dir))
    response = client.get("/ready")
    assert response.status_code == 200
    ReadinessResponse.model_validate(response.json())
    assert response.json() == {"status": "ready"}
