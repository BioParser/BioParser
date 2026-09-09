from importlib import import_module
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

app_module = import_module("bioparser.api.app")


def test_ready_no_deps(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("ARTIFACT_STORAGE_PATH", raising=False)
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_redis_unavailable(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "65000")

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
    monkeypatch.setenv("ARTIFACT_STORAGE_PATH", str(bad))
    response = client.get("/ready")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "storage" in detail["unavailable"]
