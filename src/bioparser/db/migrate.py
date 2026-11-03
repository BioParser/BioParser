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

from bioparser.db.bootstrap import bootstrap
from bioparser.db.engine import sync_database_url


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
    """Create the application role, then upgrade BIOPARSER_DATABASE_URL.

    The role's password is the password in that URL.
    """
    url = os.environ.get("BIOPARSER_DATABASE_URL", "")
    if not url:
        sys.exit("BIOPARSER_DATABASE_URL is required")
    try:
        password = make_url(sync_database_url(url)).password
    except (ArgumentError, ValueError):
        sys.exit("BIOPARSER_DATABASE_URL must use the postgresql scheme")
    if not password:
        sys.exit("BIOPARSER_DATABASE_URL must include the password")
    try:
        bootstrap(url, password)
        upgrade(url)
    except Exception as exc:  # noqa: BLE001 — any failure text can contain the URL
        sys.exit(_public_failure(url, exc))
    print("migrated to head")


def _public_failure(url: str, exc: Exception) -> str:
    """Failure text with the database URL and its password removed."""
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
