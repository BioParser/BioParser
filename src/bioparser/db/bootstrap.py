"""Create the login the API and workers use, and grant it the migrator's objects.

The migrator stays a superuser so it can change the schema. The application role
cannot. Default privileges are recorded before Alembic creates tables, so a
later revision needs no grant of its own.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from bioparser.db.engine import sync_database_url

# Compose and CI log in with this name. It is not the image superuser.
APP_ROLE = "bioparser_app"


def bootstrap(url: str, password: str) -> None:
    """Grant APP_ROLE password and the ordinary privileges on url's database.

    url is the migrator's login. It must be allowed to create roles. A second
    call sets the password again and leaves the privileges in place.
    """
    if not password:
        raise ValueError("application password is required")
    engine = create_engine(sync_database_url(url))
    try:
        with engine.begin() as connection:
            _ensure_role(connection, password)
            _grant(connection)
    finally:
        engine.dispose()


def _ensure_role(connection: Connection, password: str) -> None:
    """Create the application role, or force its privileges and password."""
    exists = connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
        {"role": APP_ROLE},
    ).scalar()
    if exists:
        template = "ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE"
    else:
        template = "CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE"
    # CREATE ROLE cannot take a bound password. format(%L) quotes it, and the
    # result is executed as SQL rather than as a statement with bind markers.
    _run(connection, template, APP_ROLE, password)


def _grant(connection: Connection) -> None:
    """Let the application role use objects the current login creates."""
    database = str(connection.execute(text("SELECT current_database()")).scalar_one())
    migrator = str(connection.execute(text("SELECT current_user")).scalar_one())
    _run(connection, "GRANT CONNECT ON DATABASE %I TO %I", database, APP_ROLE)
    _run(connection, "GRANT USAGE ON SCHEMA public TO %I", APP_ROLE)
    _run(connection, "REVOKE CREATE ON SCHEMA public FROM %I", APP_ROLE)
    for privilege in (
        "GRANT ALL PRIVILEGES ON TABLES TO %I",
        "GRANT ALL PRIVILEGES ON SEQUENCES TO %I",
        "GRANT EXECUTE ON FUNCTIONS TO %I",
    ):
        _run(
            connection,
            f"ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public {privilege}",
            migrator,
            APP_ROLE,
        )
    _run(connection, "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO %I", APP_ROLE)
    _run(connection, "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO %I", APP_ROLE)
    _run(connection, "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO %I", APP_ROLE)


def _run(connection: Connection, template: str, *args: str) -> None:
    """Run one identifier-quoted statement. The template's %I and %L are Postgres format."""
    if len(args) == 1:
        rendered = connection.execute(
            text("SELECT format(CAST(:template AS text), CAST(:a AS text))"),
            {"template": template, "a": args[0]},
        ).scalar_one()
    elif len(args) == 2:
        rendered = connection.execute(
            text("SELECT format(CAST(:template AS text), CAST(:a AS text), CAST(:b AS text))"),
            {"template": template, "a": args[0], "b": args[1]},
        ).scalar_one()
    else:
        raise ValueError("bootstrap statement needs one or two arguments")
    connection.exec_driver_sql(str(rendered))
