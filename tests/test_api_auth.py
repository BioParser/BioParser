import asyncio
import logging
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.routing import Route
from starlette.types import Message, Scope
from stubs import StubArtifactStorage, StubJobs, StubParseQueue

from bioparser.api.app import app
from bioparser.api.auth import PUBLIC_PATHS, is_public_path
from bioparser.api.config import ApiSettings
from bioparser.api.errors import ErrorResponse

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
CANARY_API_KEY = "sk-super-secret-canary-api-key-8899"

ROUTES = [route for route in app.routes if isinstance(route, Route)]


def _example_path(route: Route) -> str:
    path = route.path
    if "{job_id}" in path:
        path = path.replace("{job_id}", str(uuid4()))
    if "{checksum}" in path:
        path = path.replace("{checksum}", "a" * 64)
    return path


@pytest.fixture
def auth_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIOPARSER_AUTH_DISABLED", "false")
    monkeypatch.setenv("BIOPARSER_API_KEY", CANARY_API_KEY)


def test_unauthenticated_request_returns_401(auth_enabled: None) -> None:
    client = TestClient(app)

    # /api/extractions
    response = client.post(
        "/api/extractions",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "ApiKey"
    ErrorResponse.model_validate(response.json())
    assert response.json() == {
        "detail": {"code": "unauthorized", "message": "Invalid or missing API key"}
    }

    # /api/jobs/{job_id}
    response = client.get(f"/api/jobs/{uuid4()}")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "ApiKey"
    ErrorResponse.model_validate(response.json())
    assert response.json() == {
        "detail": {"code": "unauthorized", "message": "Invalid or missing API key"}
    }

    # /extract
    response = client.post(
        "/extract",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "ApiKey"
    ErrorResponse.model_validate(response.json())
    assert response.json() == {
        "detail": {"code": "unauthorized", "message": "Invalid or missing API key"}
    }


def test_invalid_api_key_returns_401(auth_enabled: None) -> None:
    client = TestClient(app, headers={"X-API-Key": "wrong-key"})

    response = client.get(f"/api/jobs/{uuid4()}")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "ApiKey"
    ErrorResponse.model_validate(response.json())
    assert response.json() == {
        "detail": {"code": "unauthorized", "message": "Invalid or missing API key"}
    }


def test_valid_api_key_succeeds(
    auth_enabled: None,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    client = TestClient(app, headers={"X-API-Key": CANARY_API_KEY})

    # Should get past auth and hit the route logic (returns 404 for nonexistent job)
    response = client.get(f"/api/jobs/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "job_not_found"

    # Should submit successfully
    response = client.post(
        "/api/extractions",
        files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 202


def test_health_and_ready_accessible_without_api_key(auth_enabled: None) -> None:
    client = TestClient(app)

    # No X-API-Key header sent
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    response = client.get("/ready")
    # /ready is either 200 or 503 depending on dependency availability, never 401
    assert response.status_code in (200, 503)


def test_auth_disabled_allows_requests_without_key(
    monkeypatch: pytest.MonkeyPatch,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
) -> None:
    monkeypatch.setenv("BIOPARSER_AUTH_DISABLED", "true")
    monkeypatch.delenv("BIOPARSER_API_KEY", raising=False)

    client = TestClient(app)
    response = client.get(f"/api/jobs/{uuid4()}")
    assert response.status_code == 404


def test_startup_fails_when_auth_enabled_and_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BIOPARSER_AUTH_DISABLED", "false")
    monkeypatch.setenv("BIOPARSER_API_KEY", "")
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://localhost:6379/15")
    monkeypatch.setenv("BIOPARSER_ARTIFACT_STORAGE_PATH", str(tmp_path / "artifacts"))

    with pytest.raises(RuntimeError, match="startup failed: RuntimeError"), TestClient(app):
        pass

    critical_logs = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert any(
        "BIOPARSER_API_KEY must be set when BIOPARSER_AUTH_DISABLED is false" in r.getMessage()
        for r in critical_logs
    )


def test_startup_logs_warning_when_auth_disabled(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BIOPARSER_AUTH_DISABLED", "true")
    monkeypatch.delenv("BIOPARSER_API_KEY", raising=False)
    monkeypatch.setenv("BIOPARSER_REDIS_URL", "redis://localhost:6379/15")
    monkeypatch.setenv("BIOPARSER_ARTIFACT_STORAGE_PATH", str(tmp_path / "artifacts"))
    for name in ("mineru", "vllm", "parse_queue", "artifact_storage"):
        monkeypatch.setattr(app.state, name, None, raising=False)

    with TestClient(app):
        pass

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "authentication is disabled (BIOPARSER_AUTH_DISABLED=true)" in r.getMessage()
        for r in warnings
    )


def test_api_key_never_appears_in_logs_or_errors(
    auth_enabled: None,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobs, StubParseQueue, list[str]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)

    # 1. Request with bad key
    client_bad = TestClient(app, headers={"X-API-Key": "attempt-with-wrong-key"})
    resp_bad = client_bad.get(f"/api/jobs/{uuid4()}")
    assert resp_bad.status_code == 401
    assert CANARY_API_KEY not in resp_bad.text

    # 2. Request with valid key
    client_good = TestClient(app, headers={"X-API-Key": CANARY_API_KEY})
    resp_good = client_good.get(f"/api/jobs/{uuid4()}")
    assert resp_good.status_code == 404

    logs = " ".join(r.getMessage() for r in caplog.records) + " " + capsys.readouterr().err
    assert CANARY_API_KEY not in logs


def test_openapi_declares_security_scheme() -> None:
    schema = app.openapi()
    security_schemes = schema["components"]["securitySchemes"]

    assert "ApiKeyAuth" in security_schemes
    assert security_schemes["ApiKeyAuth"] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "Shared API key required for all /api/* and /extract operations.",
    }

    # Every operation outside PUBLIC_PATHS must declare ApiKeyAuth
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method.lower() in ("get", "post", "put", "delete", "patch"):
                if path in PUBLIC_PATHS:
                    assert "security" not in operation, f"{method.upper()} {path} should be public"
                else:
                    assert operation.get("security") == [{"ApiKeyAuth": []}], (
                        f"{method.upper()} {path} must require ApiKeyAuth"
                    )


@pytest.mark.parametrize("path", ["/api/extractions", "/extract"])
def test_a_request_without_the_key_is_refused_before_its_body_is_read(
    auth_enabled: None, path: str
) -> None:
    """FastAPI reads an upload before it runs the route's dependencies. The middleware runs first."""
    reads = 0
    sent: list[Message] = []

    async def receive() -> Message:
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": b"0" * 65536, "more_body": True}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"content-type", b"multipart/form-data; boundary=boundary"),
            (b"content-length", b"65536"),
        ],
    }

    asyncio.run(app(scope, receive, send))

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 401
    assert reads == 0


def test_a_non_ascii_key_gets_401_not_500(auth_enabled: None) -> None:
    client = TestClient(app)
    response = client.get(
        f"/api/jobs/{uuid4()}",
        headers=[(b"X-API-Key", b"\xe9t\xe9-invalid")],
    )
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "ApiKey"
    ErrorResponse.model_validate(response.json())


@pytest.mark.parametrize(
    "bad_key",
    [
        "short",
        "password",
        "a" * 31,
        "pässwörd-" + "a" * 25,
        "key with spaces in the middle   ",
    ],
)
def test_short_and_non_ascii_keys_fail_validation(bad_key: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ApiSettings.model_validate({"api_key": bad_key})
    errors = exc_info.value.errors()
    assert any("at least 32 visible ASCII characters" in e["msg"] for e in errors)


@pytest.mark.parametrize("route", ROUTES, ids=lambda route: route.path)
def test_every_route_needs_the_key_unless_it_is_public(auth_enabled: None, route: Route) -> None:
    """Secure by default: a new route needs the key even if it forgets the dependency."""
    client = TestClient(app, raise_server_exceptions=False)

    for method in sorted(route.methods or ()):
        if method in ("HEAD", "OPTIONS"):
            continue
        status = client.request(method, _example_path(route)).status_code
        assert (status == 401) == (not is_public_path(route.path)), (method, route.path, status)


def test_every_public_path_is_a_route() -> None:
    route_paths = {route.path for route in app.routes if isinstance(route, Route)}
    assert PUBLIC_PATHS.issubset(route_paths | {"/openapi.json", "/docs/oauth2-redirect"})
