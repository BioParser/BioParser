import logging
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from stubs import StubArtifactStorage, StubJobStateStore, StubParseQueue

from bioparser.api.app import app
from bioparser.api.errors import ErrorResponse

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
CANARY_API_KEY = "sk-super-secret-canary-api-key-8899"


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
    stub_api_adapters: tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]],
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
    stub_api_adapters: tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]],
) -> None:
    monkeypatch.setenv("BIOPARSER_AUTH_DISABLED", "true")
    monkeypatch.delenv("BIOPARSER_API_KEY", raising=False)

    client = TestClient(app)
    response = client.get(f"/api/jobs/{uuid4()}")
    assert response.status_code == 404


def test_startup_fails_when_auth_enabled_and_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("BIOPARSER_AUTH_DISABLED", "false")
    monkeypatch.setenv("BIOPARSER_API_KEY", "")

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
) -> None:
    monkeypatch.setenv("BIOPARSER_AUTH_DISABLED", "true")
    monkeypatch.delenv("BIOPARSER_API_KEY", raising=False)

    with TestClient(app):
        pass

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "authentication is disabled (BIOPARSER_AUTH_DISABLED=true)" in r.getMessage()
        for r in warnings
    )


def test_api_key_never_appears_in_logs_or_errors(
    auth_enabled: None,
    stub_api_adapters: tuple[StubArtifactStorage, StubJobStateStore, StubParseQueue, list[str]],
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

    # Protected routes must have ApiKeyAuth security requirement
    assert schema["paths"]["/api/extractions"]["post"].get("security") == [{"ApiKeyAuth": []}]
    assert schema["paths"]["/api/jobs/{job_id}"]["get"].get("security") == [{"ApiKeyAuth": []}]
    assert schema["paths"]["/extract"]["post"].get("security") == [{"ApiKeyAuth": []}]

    # Public routes must not require security
    assert "security" not in schema["paths"]["/health"]["get"]
    assert "security" not in schema["paths"]["/ready"]["get"]
