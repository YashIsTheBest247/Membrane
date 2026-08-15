"""Async database access.

SQLite (aiosqlite) is the zero-infrastructure default so the proxy runs with a
single command; PostgreSQL (asyncpg) is used by docker-compose and Cloud Run.
The schema is identical either way.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from .config import get_settings

_engine: AsyncEngine | None = None
_session_factory: sessionmaker | None = None

# libpq understands these; asyncpg does not, and raises TypeError on an
# unexpected keyword rather than ignoring it.
_LIBPQ_ONLY = {"channel_binding", "target_session_attrs", "options"}


def _normalise_dsn(url: str) -> tuple[str, dict]:
    """Accept the plain DSN forms people paste and make them async.

    Managed Postgres providers hand out a libpq connection string —
    `postgres://…?sslmode=require` is what Render, Neon and Supabase all print
    on their dashboard. Two things in that are wrong for us: the scheme
    selects a synchronous driver we do not install, and `sslmode` is a libpq
    spelling that asyncpg rejects outright. Both are translated here so the
    string can be pasted in unedited.

    Returns the DSN and any connect_args that had to be lifted out of it.
    """
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite:///"):
        url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

    connect_args: dict = {}
    if url.startswith("postgresql+asyncpg://") and "?" in url:
        parts = urlsplit(url)
        kept: list[tuple[str, str]] = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key == "sslmode":
                # asyncpg spells it `ssl`, and takes the same vocabulary.
                connect_args["ssl"] = False if value == "disable" else value
            elif key not in _LIBPQ_ONLY:
                kept.append((key, value))
        url = urlunsplit((
            parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment,
        ))

    return url, connect_args


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        dsn, connect_args = _normalise_dsn(settings.database_url)
        kwargs: dict = {"echo": False, "future": True}
        if connect_args:
            kwargs["connect_args"] = connect_args
        if not dsn.startswith("sqlite"):
            # Managed Postgres plans cap connections low, and a platform that
            # scales to zero will hand back stale sockets on wake.
            kwargs.update(pool_size=5, max_overflow=10, pool_pre_ping=True,
                          pool_recycle=300)
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
