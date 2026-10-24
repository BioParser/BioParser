"""Migration coverage. The upgrade test needs BIOPARSER_TEST_DATABASE_URL."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from bioparser.db.migrate import alembic_config, main, sync_database_url

_COLUMNS = {
    "job_id": "NO",
    "schema_version": "NO",
    "status": "NO",
    "parser": "NO",
    "extractor": "NO",
    "pdf_ref": "NO",
    "parse_result_ref": "YES",
    "extract_result_ref": "YES",
    "error_code": "YES",
    "claim_counts": "NO",
}


def test_sync_url_selects_psycopg() -> None:
    url = "postgresql://bioparser:s3cret@postgres:5432/bioparser"
    assert sync_database_url(url) == "postgresql+psycopg://bioparser:s3cret@postgres:5432/bioparser"


def test_sync_url_keeps_an_existing_driver() -> None:
    url = "postgresql+psycopg://bioparser@postgres/bioparser"
    assert sync_database_url(url) == url


def test_sync_url_rejects_another_scheme() -> None:
    with pytest.raises(ValueError, match="postgresql"):
        sync_database_url("redis://localhost/0")


def test_main_requires_a_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BIOPARSER_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="BIOPARSER_DATABASE_URL is required"):
        main()


def test_main_hides_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "BIOPARSER_DATABASE_URL", "postgresql://bioparser:s3cret@postgres:5432/bioparser"
    )

    def fail(url: str) -> None:
        raise RuntimeError(f"could not connect to {url}")

    monkeypatch.setattr("bioparser.db.migrate.upgrade", fail)
    with pytest.raises(SystemExit, match="database migration failed") as raised:
        main()
    assert "s3cret" not in str(raised.value)


@pytest.mark.integration
def test_jobs_migration_upgrades_an_empty_database(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = os.environ.get("BIOPARSER_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("BIOPARSER_TEST_DATABASE_URL is not set")

    admin_url = make_url(sync_database_url(raw)).set(database="postgres")
    name = f"migrate_{uuid4().hex}"
    _create_database(admin_url.render_as_string(hide_password=False), name)
    target = make_url(sync_database_url(raw)).set(database=name).set(drivername="postgresql")
    monkeypatch.setenv("BIOPARSER_DATABASE_URL", target.render_as_string(hide_password=False))
    try:
        main()
        _assert_jobs_table(admin_url.render_as_string(hide_password=False), name)
        main()
        _assert_jobs_table(admin_url.render_as_string(hide_password=False), name)
        command.downgrade(alembic_config(os.environ["BIOPARSER_DATABASE_URL"]), "base")
        assert _column_nullability(admin_url.render_as_string(hide_password=False), name) == {}
        main()
        _assert_jobs_table(admin_url.render_as_string(hide_password=False), name)
    finally:
        _drop_database(admin_url.render_as_string(hide_password=False), name)


def _create_database(admin_url: str, name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine.dispose()


def _drop_database(admin_url: str, name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(
            text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"),
            {"name": name},
        )
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    engine.dispose()


def _assert_jobs_table(admin_url: str, name: str) -> None:
    assert _column_nullability(admin_url, name) == _COLUMNS


def _column_nullability(admin_url: str, name: str) -> dict[str, str]:
    url = make_url(admin_url).set(database=name)
    engine = create_engine(url)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'jobs'"
            )
        )
        found = {str(column): str(nullable) for column, nullable in rows}
    engine.dispose()
    return found
