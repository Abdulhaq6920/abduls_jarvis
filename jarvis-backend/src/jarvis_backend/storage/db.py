"""Async database access (SQLAlchemy 2.x).

SQLite is the zero-config default for a single desktop install; PostgreSQL is the
production target. Application code only sees :class:`Database`, so the engine is a
configuration choice, not a code change.
"""
from __future__ import annotations

import ssl
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import AsyncIterator

from sqlalchemy import DateTime, event, text
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.types import TypeDecorator

from jarvis_backend.config import Settings


def utcnow() -> datetime:
    return datetime.now(UTC)


def _postgres_connect_args(settings: Settings) -> dict:
    """asyncpg TLS arguments from ``JARVIS_DATABASE_SSL`` (+ optional CA bundle).

    ``require`` encrypts but does not verify the server; ``verify-full`` also checks the
    certificate chain and host name and is the only mode that stops a man-in-the-middle.
    """
    mode = settings.database_ssl
    if mode == "disable":
        return {"ssl": False}
    if mode in ("verify-ca", "verify-full"):
        ctx = ssl.create_default_context(cafile=settings.database_ca_file or None)
        ctx.check_hostname = mode == "verify-full"
        return {"ssl": ctx}
    return {"ssl": mode}  # "prefer" | "require"


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend.

    SQLite silently drops tzinfo; without this, comparing values read from SQLite with
    ``utcnow()`` raises. We normalise to UTC on write and re-attach UTC on read.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime passed to UTCDateTime; use timezone-aware UTC.")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Database:
    """Owns the engine and hands out sessions."""

    def __init__(self, settings: Settings) -> None:
        self.url = settings.effective_database_url
        self.is_sqlite = settings.is_sqlite
        kwargs: dict = {"pool_pre_ping": True}
        if not self.is_sqlite:
            kwargs.update(
                pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow,
                pool_recycle=1800,  # drop connections before a proxy/firewall silently does
                connect_args=_postgres_connect_args(settings),
            )
        else:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine: AsyncEngine = create_async_engine(self.url, **kwargs)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        if self.is_sqlite:
            @event.listens_for(self.engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - trivial
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")      # readers don't block the writer
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=5000")
                cur.close()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A session inside a transaction: commits on success, rolls back on error."""
        async with self._sessions() as session:
            async with session.begin():
                yield session

    async def create_schema(self) -> None:
        from jarvis_backend.storage.models import Base

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def dispose(self) -> None:
        await self.engine.dispose()
