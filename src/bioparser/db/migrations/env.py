"""Alembic environment. The URL is set by bioparser-migrate, not by an ini file."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from bioparser.db.documents import Document  # noqa: F401
from bioparser.db.jobs import Job

# Import every table class. Importing it registers the table, and the classes share this metadata.
target_metadata = Job.metadata

config = context.config


def run_migrations_offline() -> None:
    """Run migrations without opening a connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
