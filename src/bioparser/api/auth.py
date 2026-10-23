from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Security
from fastapi.security import APIKeyHeader

from bioparser.api import config
from bioparser.api.errors import UNAUTHORIZED, http_error

api_key_header = APIKeyHeader(
    name="X-API-Key",
    scheme_name="ApiKeyAuth",
    auto_error=False,
    description="Shared API key required for all /api/* and /extract operations.",
)


async def require_api_key(
    header_value: Annotated[str | None, Security(api_key_header)] = None,
) -> None:
    settings = config.get_api_settings()
    if settings.auth_disabled:
        return

    expected_key = settings.api_key.get_secret_value() if settings.api_key else None
    if (
        not expected_key
        or not header_value
        or not secrets.compare_digest(header_value, expected_key)
    ):
        raise http_error(
            status_code=401,
            detail=UNAUTHORIZED,
            headers={"WWW-Authenticate": "ApiKey"},
        )
