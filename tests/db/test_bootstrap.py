"""Application-role coverage. The Postgres test needs BIOPARSER_TEST_DATABASE_URL.

The role is cluster-wide. migrate copies the password from that URL onto it.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from bioparser.db.bootstrap import APP_ROLE
from bioparser.db.engine import sync_database_url
from bioparser.db.migrate import main, upgrade


@pytest.mark.integration
def test_app_role_can_use_tables_and_cannot_change_the_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = os.environ.get("BIOPARSER_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("BIOPARSER_TEST_DATABASE_URL is not set")

    password = make_url(sync_database_url(raw)).password
    if not password:
        pytest.skip("BIOPARSER_TEST_DATABASE_URL must include the password")

    admin_url = make_url(sync_database_url(raw)).set(database="postgres")
    name = f"bootstrap_{uuid4().hex}"
    admin = admin_url.render_as_string(hide_password=False)
    _create_database(admin, name)
    target = make_url(sync_database_url(raw)).set(database=name).set(drivername="postgresql")
    monkeypatch.setenv("BIOPARSER_DATABASE_URL", target.render_as_string(hide_password=False))
    try:
        upgrade(target.render_as_string(hide_password=False))
        main()
        main()

        app_url = target.set(drivername="postgresql+psycopg", username=APP_ROLE, password=password)
        _assert_role(admin, name)
        _assert_table_privileges(admin, name, "jobs")
        _assert_table_use(app_url.render_as_string(hide_password=False))
        _assert_later_table(admin, name, app_url.render_as_string(hide_password=False))
        _assert_table_privileges(admin, name, "privilege_probe")
        _assert_schema_changes_fail(app_url.render_as_string(hide_password=False))
    finally:
        _drop_database(admin, name)


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


def _assert_role(admin_url: str, name: str) -> None:
    url = make_url(admin_url).set(database=name)
    engine = create_engine(url)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin "
                "FROM pg_roles WHERE rolname = :role"
            ),
            {"role": APP_ROLE},
        ).one()
    engine.dispose()
    assert row == (False, False, False, True)


def _assert_table_privileges(admin_url: str, name: str, table: str) -> None:
    held = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
    withheld = ("REFERENCES", "TRIGGER", "MAINTAIN")
    url = make_url(admin_url).set(database=name)
    engine = create_engine(url)
    with engine.connect() as connection:
        for privilege in (*held, *withheld):
            allowed = connection.execute(
                text("SELECT has_table_privilege(:role, :table, :privilege)"),
                {"role": APP_ROLE, "table": table, "privilege": privilege},
            ).scalar_one()
            assert allowed == (privilege in held)
    engine.dispose()


def _assert_table_use(app_url: str) -> None:
    job_id = str(uuid4())
    engine = create_engine(app_url)
    with engine.begin() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == APP_ROLE
        connection.execute(
            text(
                "INSERT INTO jobs (job_id, schema_version, status, parser, extractor, pdf_ref) "
                "VALUES (:job_id, 1, 'queued', 'default', 'default', 'pdf')"
            ),
            {"job_id": job_id},
        )
        connection.execute(
            text("UPDATE jobs SET status = 'parsing' WHERE job_id = :job_id"),
            {"job_id": job_id},
        )
        connection.execute(text("DELETE FROM jobs WHERE job_id = :job_id"), {"job_id": job_id})
        connection.execute(text("TRUNCATE jobs"))
    engine.dispose()


def _assert_later_table(admin_url: str, name: str, app_url: str) -> None:
    admin = create_engine(make_url(admin_url).set(database=name))
    with admin.begin() as connection:
        connection.execute(text("CREATE TABLE privilege_probe (id integer PRIMARY KEY)"))
    admin.dispose()

    app = create_engine(app_url)
    with app.begin() as connection:
        connection.execute(text("INSERT INTO privilege_probe (id) VALUES (1)"))
        connection.execute(text("DELETE FROM privilege_probe WHERE id = 1"))
        connection.execute(text("TRUNCATE privilege_probe"))
    app.dispose()


def _assert_schema_changes_fail(app_url: str) -> None:
    engine = create_engine(app_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        for statement in (
            "CREATE TABLE denied (id integer)",
            "CREATE ROLE denied_role",
            "CREATE DATABASE denied_database",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                connection.execute(text(statement))
    engine.dispose()
