"""Engine and sessions for the personalization database (ADR-011).

Separate URL, separate engine, separate Alembic history, separate declarative
base. No foreign key crosses between the two databases -- Postgres cannot
express one -- so ``subject_id`` over there is an opaque value rather than a
reference, and the only thing keeping the two in step is the erasure sweep in
``app/core/erasure.py``.

**This module has one owner.** It lives apart from ``core/database.py``, which
every module legitimately imports, precisely so that "nothing outside
``app.modules.personalization`` opens the second database" can be an
import-linter contract rather than a grep: the linter is module-granular and
cannot forbid a *function*. See ``the-signals-database-has-one-owner`` in
`.importlinter`. The one exception is ``app.main``, the composition root, which
disposes the pool on shutdown.

Readiness reaches this the legal way, through
``PersonalizationService.health()`` -- api -> modules is a permitted direction,
so the contract needs no carve-out.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.database import worker_async_session


class PersonalizationNotConfiguredError(RuntimeError):
    """Raised when the second database is asked for and is not configured.

    Raising rather than returning ``None`` is deliberate: a missing
    ``settings.personalization_enabled`` check then fails at the call site with
    a name that says what is wrong, instead of surfacing three frames later as
    an attribute error on ``None``.
    """


@lru_cache(maxsize=1)
def get_signals_engine() -> AsyncEngine:
    """Engine for the API process."""
    settings = get_settings()
    if settings.signals_database_url is None:
        raise PersonalizationNotConfiguredError(
            "The personalization database is not configured. Check "
            "settings.personalization_enabled before reaching for a signals session."
        )
    return create_async_engine(
        settings.signals_async_url,
        echo=settings.db_echo,
        pool_size=settings.signals_db_pool_size,
        max_overflow=settings.signals_db_max_overflow,
        pool_pre_ping=True,
        # Neon closes idle connections; recycle below their timeout.
        pool_recycle=280,
    )


@lru_cache(maxsize=1)
def get_signals_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_signals_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_signals_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency, with the same commit/rollback discipline as `get_db`."""
    async with get_signals_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def worker_signals_session() -> AsyncIterator[AsyncSession]:
    """A signals session for a Celery task, on an engine it owns and disposes.

    The rule in `core.database.worker_async_session` applies here identically
    and for the same reason: `asyncio.run` creates and destroys an event loop
    while asyncpg's connections stay bound to the loop that opened them, so a
    cached engine hands the second task a pool belonging to a loop that no
    longer exists. It now applies *twice* in any task that touches both
    databases -- which is what `worker_both_sessions` is for.
    """
    settings = get_settings()
    if settings.signals_database_url is None:
        raise PersonalizationNotConfiguredError("The personalization database is not configured.")
    engine = create_async_engine(
        settings.signals_async_url,
        echo=settings.db_echo,
        poolclass=NullPool,
    )
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@asynccontextmanager
async def worker_both_sessions() -> AsyncIterator[tuple[AsyncSession, AsyncSession]]:
    """Both databases, both on engines this call owns and disposes.

    Exists so the correct thing is the convenient thing -- the same reasoning
    that produced `worker_async_session` after the loop-binding bug was hit in
    three separate places and looked like a different problem each time.

    **There is no transaction spanning the two and there cannot be.** Commit
    order is therefore the caller's decision, and it is load-bearing: see
    `app/core/erasure.py`, where committing the primary first would mark a
    deletion done while the data still existed.
    """
    async with worker_async_session() as primary, worker_signals_session() as signals:
        yield primary, signals


async def check_signals_database() -> bool | None:
    """Readiness. ``None`` when the feature is not configured.

    Absent is not degraded. A deployment that never turned personalization on
    must not report a dependency as down, or the readiness probe becomes noise
    that operators learn to ignore.
    """
    if not get_settings().personalization_enabled:
        return None
    try:
        async with get_signals_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def dispose_signals_engine() -> None:
    """Close the pool on shutdown, without building one to close.

    On a deployment with the feature off `get_signals_engine()` raises; on one
    where it is on but nothing used it, constructing a pool at shutdown is pure
    waste. The cache introspection answers both.
    """
    if get_signals_engine.cache_info().currsize:
        await get_signals_engine().dispose()
