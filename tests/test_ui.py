from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bioparser.api.app import app

INDEX = "<!doctype html><div id=root></div>"
ASSET = "console.log('ui');"


@pytest.fixture
def ui_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX, encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text(ASSET, encoding="utf-8")
    # Outside dist: no request may read it.
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    monkeypatch.setenv("BIOPARSER_UI_DIST_PATH", str(dist))
    return dist


def test_ui_not_configured(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.delenv("BIOPARSER_UI_DIST_PATH", raising=False)
    assert client.get("/ui/").status_code == 404


def test_ui_dist_without_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setenv("BIOPARSER_UI_DIST_PATH", str(tmp_path))
    assert client.get("/ui/").status_code == 404


def test_ui_root_redirects(ui_dist: Path, client: TestClient) -> None:
    response = client.get("/ui", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/ui/"


@pytest.mark.parametrize("path", ["/ui/", "/ui/jobs/abc", "/ui/key"])
def test_ui_paths_return_index(ui_dist: Path, client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"


def test_ui_serves_asset(ui_dist: Path, client: TestClient) -> None:
    response = client.get("/ui/assets/index-abc123.js")
    assert response.status_code == 200
    assert response.text == ASSET
    assert "javascript" in response.headers["content-type"]
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_ui_missing_asset_is_404(ui_dist: Path, client: TestClient) -> None:
    assert client.get("/ui/assets/missing.js").status_code == 404


@pytest.mark.parametrize(
    "path",
    ["/ui/..%2fsecret.txt", "/ui/assets/..%2f..%2fsecret.txt", "/ui/%2e%2e/secret.txt"],
)
def test_ui_never_serves_files_outside_dist(ui_dist: Path, client: TestClient, path: str) -> None:
    response = client.get(path)
    assert "top secret" not in response.text


@pytest.fixture
def auth_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIOPARSER_AUTH_DISABLED", "false")
    monkeypatch.setenv("BIOPARSER_API_KEY", "ui-test-api-key-at-least-32-characters")


@pytest.mark.parametrize("path", ["/ui/", "/ui/jobs/abc", "/ui/assets/index-abc123.js"])
def test_ui_needs_no_api_key(ui_dist: Path, auth_enabled: None, path: str) -> None:
    response = TestClient(app).get(path)  # no X-API-Key header
    assert response.status_code == 200


def test_ui_root_redirect_needs_no_api_key(ui_dist: Path, auth_enabled: None) -> None:
    assert TestClient(app).get("/ui", follow_redirects=False).status_code == 307


def test_ui_prefix_opens_only_the_ui(ui_dist: Path, auth_enabled: None) -> None:
    client = TestClient(app)
    # An encoded "../" stays under /ui/: the UI route answers, not the job route.
    response = client.get(f"/ui/..%2fapi%2fjobs%2f{uuid4()}")
    assert response.text == INDEX
    # The prefix is "/ui/", so a path that only starts with "/ui" still needs the key.
    assert client.get("/uix").status_code == 401
