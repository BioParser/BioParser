"""The process-wide async Postgres connection.

Every query module uses this pool. Job state is the first. Alembic stays on the
sync psycopg driver; this pool uses asyncpg because the API and the workers
call it from their event loops.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

# Compose and the settings use a bare postgresql URL. The async engine needs the driver.
_POSTGRES_PREFIXES = ("postgresql+psycopg://", "postgresql://", "postgres://")


class DatabaseError(RuntimeError):
    """The pool could not borrow a connection or commit it. The message has no URL."""


_engine: AsyncEngine | None = None
_engine_url: str | None = None


def async_database_url(url: str) -> str:
    """Return url with the asyncpg driver selected."""
    if url.startswith("postgresql+asyncpg://"):
        return url
    for prefix in _POSTGRES_PREFIXES:
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    raise ValueError("database URL must use the postgresql scheme")


async def open(url: str) -> None:
    """Open this process's pool. The same URL keeps it. A different URL is refused."""
    global _engine, _engine_url
    normalized = async_database_url(url)
    if _engine is not None:
        if normalized != _engine_url:
            raise RuntimeError("Postgres is already open with a different URL")
        return
    _engine = create_async_engine(
        normalized,
        pool_pre_ping=True,
        pool_timeout=5,
        connect_args={"timeout": 5, "command_timeout": 5},
    )
    _engine_url = normalized


async def aclose() -> None:
    """Dispose this process's pool. Safe when `open` was never called."""
    global _engine, _engine_url
    engine = _engine
    _engine = None
    _engine_url = None
    if engine is not None:
        await engine.dispose()


def require() -> AsyncEngine:
    """Return the open pool. The process must have called `open` first."""
    if _engine is None:
        raise RuntimeError("Postgres is not open")
    return _engine


# What borrowing a connection or committing can raise. Pool exhaustion is a
# SQLAlchemy error, not the builtin TimeoutError. A refused connection is an OSError.
_BORROW_AND_COMMIT_ERRORS = (SQLAlchemyError, OSError, TimeoutError)


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    """Borrow one pooled connection. A clean exit commits; an exception rolls back.

    Query functions take this connection, so a caller can run several of them
    in one transaction and pass the same connection through. A driver failure
    becomes DatabaseError. The message names the error type and not the URL.

    Only the borrow, the commit, and SQLAlchemy's own errors in the body are
    converted. An OSError or TimeoutError raised by other code in the body
    passes through unchanged, so keep the body to database calls anyway.
    """
    try:
        connection = await require().connect()
    except _BORROW_AND_COMMIT_ERRORS as exc:
        raise DatabaseError(f"Postgres query failed: {type(exc).__name__}") from exc
    try:
        try:
            yield connection
        except SQLAlchemyError as exc:
            raise DatabaseError(f"Postgres query failed: {type(exc).__name__}") from exc
        try:
            await connection.commit()
        except _BORROW_AND_COMMIT_ERRORS as exc:
            raise DatabaseError(f"Postgres query failed: {type(exc).__name__}") from exc
    finally:
        # Closing rolls back a transaction that did not commit.
        await connection.close()
