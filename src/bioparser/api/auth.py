from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from bioparser.api import config
from bioparser.api.errors import UNAUTHORIZED, ErrorResponse, http_error

API_KEY_HEADER = "X-API-Key"

# The probes (Docker's HEALTHCHECK sends no key), the pages Swagger UI loads before the
# user can enter a key, and the web UI. Every other path needs the key.
PUBLIC_PATHS = frozenset(
    {"/health", "/ready", "/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc", "/ui"}
)
# The built web UI under /ui/: static files that hold no data. The browser loads them
# without a key; the UI sends the key on its own API calls.
PUBLIC_PREFIX = "/ui/"


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIX)


# RFC 9110: a 401 response names the scheme the client must use.
_CHALLENGE = {"WWW-Authenticate": "ApiKey"}

api_key_header = APIKeyHeader(
    name=API_KEY_HEADER,
    scheme_name="ApiKeyAuth",
    auto_error=False,
    description="Shared API key required for all /api/* and /extract operations.",
)


def api_key_matches(value: str | None) -> bool:
    """True when authentication is off, or `value` is the configured key."""
    settings = config.get_api_settings()
    if settings.auth_disabled:
        return True
    expected = settings.api_key.get_secret_value() if settings.api_key else ""
    if not expected or not value:
        return False
    # Bytes, not str: compare_digest raises TypeError for a str with a non-ASCII
    # character, and a client can put any byte in a header.
    return secrets.compare_digest(value.encode(), expected.encode())


class ApiKeyMiddleware:
    """Pure ASGI, like TypedRequestBodyLimitMiddleware, so it runs before FastAPI reads a body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Lifespan events pass. The API has no websocket routes, so the router closes
        # any websocket. No root_path is set (Traefik keeps the path as it is), so
        # scope["path"] is the path the router matches.
        if (
            scope["type"] != "http"
            or is_public_path(scope["path"])
            or api_key_matches(Headers(scope=scope).get(API_KEY_HEADER))
        ):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            ErrorResponse(detail=UNAUTHORIZED).model_dump(),
            status_code=401,
            headers=_CHALLENGE,
        )
        # receive() is never called, so the body is never read.
        await response(scope, receive, send)


async def require_api_key(
    header_value: Annotated[str | None, Security(api_key_header)] = None,
) -> None:
    if not api_key_matches(header_value):
        raise http_error(status_code=401, detail=UNAUTHORIZED, headers=_CHALLENGE)
