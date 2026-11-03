"""The process-wide async Postgres connection.

Every query module uses this pool. Job state is the first. Alembic stays on the
sync psycopg driver; this pool uses asyncpg because the API and the workers
call it from their event loops.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

# Compose and the settings use a bare postgresql URL. Each engine needs its driver.
_SYNC_PREFIXES = ("postgresql://", "postgres://")
_ASYNC_PREFIXES = ("postgresql+psycopg://", "postgresql://", "postgres://")


class DatabaseError(RuntimeError):
    """The pool could not borrow a connection or commit it. The message has no URL."""


class DatabaseUnavailableError(DatabaseError):
    """Postgres could not be reached, or the pool could not serve a connection in time.

    Workers may defer the queue delivery. Other ``DatabaseError`` types are statement or data failures that should not be treated as an outage.
    """


def _is_transient_driver_error(exc: BaseException) -> bool:
    """True when the driver error is reachability, not a failed statement."""
    if isinstance(
        exc, (OSError, TimeoutError, PoolTimeoutError, OperationalError, InterfaceError)
    ):
        return True
    return bool(isinstance(exc, SQLAlchemyError) and getattr(exc, "connection_invalidated", False))


def _database_error_from_driver(exc: BaseException) -> DatabaseError:
    message = f"Postgres query failed: {type(exc).__name__}"
    if _is_transient_driver_error(exc):
        return DatabaseUnavailableError(message)
    return DatabaseError(message)


_engine: AsyncEngine | None = None
_engine_url: str | None = None


def sync_database_url(url: str) -> str:
    """Return url with the psycopg driver selected."""
    if url.startswith("postgresql+psycopg://"):
        return url
    for prefix in _SYNC_PREFIXES:
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    raise ValueError("database URL must use the postgresql scheme")


def async_database_url(url: str) -> str:
    """Return url with the asyncpg driver selected."""
    if url.startswith("postgresql+asyncpg://"):
        return url
    for prefix in _ASYNC_PREFIXES:
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


# Pool exhaustion is a SQLAlchemy error. A stalled query under command_timeout
# is a bare TimeoutError, which SQLAlchemy does not wrap. A refused or dropped
# connection is an OSError.
_DRIVER_ERRORS = (SQLAlchemyError, OSError, TimeoutError)


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    """Borrow one pooled connection. A clean exit commits; an exception rolls back.

    Query functions take this connection, so a caller can run several of them
    in one transaction and pass the same connection through. Logic between
    those calls is fine. Other I/O is not: it holds a pooled connection, and
    an OSError or TimeoutError from it is reported as DatabaseError.

    A driver failure, including one raised by a statement, becomes
    ``DatabaseError`` or ``DatabaseUnavailableError`` when the failure is
    transient. The message names the error type and not the URL. Any other
    error passes through.
    """
    try:
        async with require().begin() as connection:
            yield connection
    except _DRIVER_ERRORS as exc:
        raise _database_error_from_driver(exc) from exc
