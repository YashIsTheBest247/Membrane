"""Async database access.

SQLite (aiosqlite) is the zero-infrastructure default so the proxy runs with a
single command; PostgreSQL (asyncpg) is used by docker-compose and Cloud Run.
The schema is identical either way.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from .config import get_settings

_engine: AsyncEngine | None = None
_session_factory: sessionmaker | None = None


def _normalise_dsn(url: str) -> str:
    """Accept the plain DSN forms people paste and make them async."""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite:///"):
        url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    return url


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        dsn = _normalise_dsn(settings.database_url)
        kwargs: dict = {"echo": False, "future": True}
        if not dsn.startswith("sqlite"):
            kwargs.update(pool_size=5, max_overflow=10, pool_pre_ping=True)
        _engine = create_async_engine(dsn, **kwargs)
    return _engine


def get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope. Commits on success, rolls back on error."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with session_scope() as session:
        yield session


async def init_db() -> None:
    """Create tables if absent.

    Alembic owns schema evolution for real deployments (see alembic/); this
    keeps a fresh checkout runnable without a migration step.
    """
    # Import for side effects: registers every table on SQLModel.metadata.
    from . import models  # noqa: F401

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def dispose_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
