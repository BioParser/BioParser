"""Apply the database migrations shipped with this package."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import quote

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# Alembic's sync engine needs the driver in the URL. Compose and the API use
# the bare postgresql scheme.
_POSTGRES_PREFIXES = ("postgresql://", "postgres://")


def sync_database_url(url: str) -> str:
    """Return url with the psycopg driver selected."""
    if url.startswith("postgresql+psycopg://"):
        return url
    for prefix in _POSTGRES_PREFIXES:
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    raise ValueError("database URL must use the postgresql scheme")


def alembic_config(url: str) -> Config:
    """Config that loads the revisions in this package and connects to url."""
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parent / "migrations"))
    # ConfigParser treats "%" as interpolation. A URL-encoded password contains it.
    config.set_main_option("sqlalchemy.url", sync_database_url(url).replace("%", "%%"))
    return config


def upgrade(url: str) -> None:
    """Upgrade the database at url to the latest revision."""
    command.upgrade(alembic_config(url), "head")


def main() -> None:
    """Upgrade the database named by BIOPARSER_DATABASE_URL."""
    url = os.environ.get("BIOPARSER_DATABASE_URL", "")
    if not url:
        sys.exit("BIOPARSER_DATABASE_URL is required")
    try:
        upgrade(url)
    except Exception as exc:  # noqa: BLE001 — any failure text can contain the URL
        sys.exit(_public_failure(url, exc))
    print("migrated to head")


def _public_failure(url: str, exc: Exception) -> str:
    """Failure text with the database URL and password removed."""
    message = str(exc)
    try:
        parsed = make_url(sync_database_url(url))
    except (ArgumentError, ValueError):
        parsed = None
    if parsed is not None and parsed.password:
        message = message.replace(parsed.password, "***")
        message = message.replace(quote(parsed.password, safe=""), "***")
        message = message.replace(parsed.render_as_string(hide_password=False), "postgresql://***")
    return f"database migration failed: {message}"
