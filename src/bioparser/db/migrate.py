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
    """Upgrade BIOPARSER_DATABASE_URL, then create the application role.

    The role's password is POSTGRES_APP_PASSWORD. It can read alembic_version
    and cannot change it.
    """
    url = os.environ.get("BIOPARSER_DATABASE_URL", "")
    if not url:
        sys.exit("BIOPARSER_DATABASE_URL is required")
    app_password = os.environ.get("POSTGRES_APP_PASSWORD", "")
    if not app_password:
        sys.exit("POSTGRES_APP_PASSWORD is required")
    try:
        parsed = make_url(sync_database_url(url))
    except (ArgumentError, ValueError):
        sys.exit("BIOPARSER_DATABASE_URL must use the postgresql scheme")
    if parsed.password == app_password:
        sys.exit("POSTGRES_APP_PASSWORD must differ from the password in BIOPARSER_DATABASE_URL")
    try:
        upgrade(url)
        bootstrap(url, app_password)
    except Exception as exc:  # noqa: BLE001 — any failure text can contain the URL
        sys.exit(_public_failure(url, app_password, exc))
    print("migrated to head")


def _public_failure(url: str, app_password: str, exc: Exception) -> str:
    """Failure text with the database URL and both passwords removed."""
    message = str(exc)
    secrets = [app_password]
    try:
        parsed = make_url(sync_database_url(url))
    except (ArgumentError, ValueError):
        parsed = None
    if parsed is not None and parsed.password:
        secrets.append(parsed.password)
        message = message.replace(parsed.render_as_string(hide_password=False), "postgresql://***")
    for secret in sorted(secrets, key=len, reverse=True):
        message = _hide_secret(message, secret)
    return f"database migration failed: {message}"


def _hide_secret(message: str, secret: str) -> str:
    """Remove one password, including its SQL-quoted and percent-encoded forms."""
    message = message.replace(secret.replace("'", "''"), "***")
    message = message.replace(secret, "***")
    return message.replace(quote(secret, safe=""), "***")
