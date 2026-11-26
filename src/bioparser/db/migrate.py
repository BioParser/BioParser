"""Apply the database migrations shipped with this package."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import quote

from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from bioparser.db.bootstrap import bootstrap
from bioparser.db.engine import sync_database_url
from bioparser.docker_secrets import SECRETS_DIR


class _Passwords(BaseSettings):
    """Each password: a variable, or the Docker secret with that name in lower case."""

    bioparser_database_password: SecretStr | None = None
    postgres_app_password: SecretStr | None = None


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

    The login's password is in the URL or in BIOPARSER_DATABASE_PASSWORD. The role's
    password is POSTGRES_APP_PASSWORD. Compose gives both as Docker secrets. The role
    can read alembic_version and cannot change it.
    """
    url = os.environ.get("BIOPARSER_DATABASE_URL", "")
    if not url:
        sys.exit("BIOPARSER_DATABASE_URL is required")
    passwords = _Passwords(_secrets_dir=SECRETS_DIR)  # type: ignore[call-arg]
    app_secret = passwords.postgres_app_password
    app_password = app_secret.get_secret_value() if app_secret else ""
    if not app_password:
        sys.exit("POSTGRES_APP_PASSWORD is required")
    try:
        parsed = make_url(sync_database_url(url))
    except (ArgumentError, ValueError):
        sys.exit("BIOPARSER_DATABASE_URL must use the postgresql scheme")
    login = passwords.bioparser_database_password
    if login is not None and not parsed.password:
        # upgrade() and bootstrap() take a URL string. _public_failure() hides the password.
        parsed = parsed.set(password=login.get_secret_value())
        url = parsed.render_as_string(hide_password=False)
    if parsed.password == app_password:
        sys.exit("POSTGRES_APP_PASSWORD must differ from the password of the migrate login")
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
