"""Create the login the API and workers use, and grant it the migrator's objects.

The migrator stays a superuser so it can change the schema. The application role
cannot. Default privileges cover tables a later migrate creates, so that
revision needs no grant of its own.
"""

from __future__ import annotations

from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from bioparser.db.engine import sync_database_url

# Compose and CI log in with this name. It is not the image superuser.
APP_ROLE = "bioparser_app"
_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def bootstrap(url: str, password: str) -> None:
    """Grant APP_ROLE password and the ordinary privileges on url's database.

    url is the migrator's login. It must be a superuser, which the log settings
    in this transaction require. A second call sets the password again and
    leaves the privileges in place.
    """
    if not password:
        raise ValueError("application password is required")
    engine = create_engine(sync_database_url(url))
    try:
        with engine.begin() as connection:
            # This transaction's statements stay out of the server log.
            connection.exec_driver_sql("SET LOCAL log_statement = 'none'")
            connection.exec_driver_sql("SET LOCAL log_min_error_statement = 'panic'")
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
        template = "ALTER ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
    else:
        template = "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
    _execute(connection, template, sql.Identifier(APP_ROLE), sql.Literal(password))


def _grant(connection: Connection) -> None:
    """Let the application role use objects the current login creates."""
    database = str(connection.execute(text("SELECT current_database()")).scalar_one())
    migrator = str(connection.execute(text("SELECT current_user")).scalar_one())
    role = sql.Identifier(APP_ROLE)
    _execute(connection, "GRANT CONNECT ON DATABASE {} TO {}", sql.Identifier(database), role)
    _execute(connection, "GRANT USAGE ON SCHEMA public TO {}", role)
    _execute(connection, "REVOKE CREATE ON SCHEMA public FROM {}", role)
    for privilege in (
        "REVOKE ALL PRIVILEGES ON TABLES FROM {}",
        f"GRANT {_TABLE_PRIVILEGES} ON TABLES TO {{}}",
        "GRANT ALL PRIVILEGES ON SEQUENCES TO {}",
        "GRANT EXECUTE ON FUNCTIONS TO {}",
    ):
        _execute(
            connection,
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public " + privilege,
            sql.Identifier(migrator),
            role,
        )
    _execute(connection, "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}", role)
    _execute(
        connection,
        f"GRANT {_TABLE_PRIVILEGES} ON ALL TABLES IN SCHEMA public TO {{}}",
        role,
    )
    _execute(connection, "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {}", role)
    _execute(connection, "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {}", role)
    _revoke_version_writes(connection)


def _revoke_version_writes(connection: Connection) -> None:
    """Take write access to alembic_version away from the application role."""
    if connection.execute(text("SELECT to_regclass('public.alembic_version')")).scalar() is None:
        return
    _execute(
        connection,
        "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE public.alembic_version FROM {}",
        sql.Identifier(APP_ROLE),
    )


def _execute(connection: Connection, template: str, *parts: sql.Composable) -> None:
    """Run one statement. The cursor has no parameters, so '%' stays literal."""
    cursor = connection.connection.cursor()
    try:
        cursor.execute(sql.SQL(template).format(*parts))
    finally:
        cursor.close()
