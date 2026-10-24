"""The process-wide async Postgres connection.

Every query module uses this pool. Job state is the first. Alembic stays on the
sync psycopg driver; this pool uses asyncpg because the API and the workers
call it from their event loops.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

# Compose and the settings use a bare postgresql URL. The async engine needs the driver.
_POSTGRES_PREFIXES = ("postgresql+psycopg://", "postgresql://", "postgres://")


class DatabaseError(RuntimeError):
    """The pool could not borrow a connection or commit it. The message has no URL."""


_engine: AsyncEngine | None = None


def async_database_url(url: str) -> str:
    """Return url with the asyncpg driver selected."""
    if url.startswith("postgresql+asyncpg://"):
        return url
    for prefix in _POSTGRES_PREFIXES:
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    raise ValueError("database URL must use the postgresql scheme")


async def open(url: str) -> None:
    """Open this process's pool. A second call keeps the pool from the first."""
    global _engine
    if _engine is not None:
        return
    _engine = create_async_engine(
        async_database_url(url),
        pool_pre_ping=True,
        pool_timeout=5,
        connect_args={"timeout": 5, "command_timeout": 5},
    )


async def aclose() -> None:
    """Dispose this process's pool. Safe when `open` was never called."""
    global _engine
    engine = _engine
    _engine = None
    if engine is not None:
        await engine.dispose()


def require() -> AsyncEngine:
    """Return the open pool. The process must have called `open` first."""
    if _engine is None:
        raise RuntimeError("Postgres is not open")
    return _engine


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    """Borrow one pooled connection. A clean exit commits; an exception rolls back.

    Query functions take this connection, so a caller can run several of them
    in one transaction and pass the same connection through.
    """
    try:
        async with require().begin() as connection:
            yield connection
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise DatabaseError(f"Postgres query failed: {type(exc).__name__}") from exc
