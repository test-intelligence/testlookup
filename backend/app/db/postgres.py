"""Async PostgreSQL database session factory using SQLAlchemy.

The engine and session factory are constructed lazily on first use so
importing this module — and any module that imports it — does not require
``DATABASE_URL`` to be set. This is a developer-ergonomics fix: tests that
stub out the DB client previously had to prepend an env-var block to every
``pytest`` invocation just to satisfy the import-time
``create_async_engine(settings.DATABASE_URL)`` call.

Lazy strategy:

* ``get_engine()`` and ``get_session_factory()`` are @lru_cache'd; each
  builds its resource the first time it is called and returns the same
  instance forever after.
* Module-level ``engine`` and ``AsyncSessionLocal`` names are preserved
  via PEP 562 ``__getattr__`` so existing ``from app.db.postgres import
  AsyncSessionLocal`` callers work unchanged. The first reference
  triggers the lazy build; subsequent references return the cached
  instance.
* Tests that genuinely need to override the engine (e.g. point at a
  fixture DB) can monkeypatch ``get_engine.cache_clear()`` and reassign
  ``settings.DATABASE_URL`` before any code touches the engine, OR
  monkeypatch ``app.db.postgres.get_engine`` directly.

Backwards-compatible: every existing import path (``Base``, ``get_db``,
``init_db``, ``close_db``, ``engine``, ``AsyncSessionLocal``) keeps its
original signature.
"""
from functools import lru_cache
from typing import Any, AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


def _pool_size() -> int:
    if settings.PG_POOL_SIZE is not None:
        return settings.PG_POOL_SIZE
    # Production default bumped from 20 → 40 in 2026-05-16 (Phase 2.3 of
    # the scalable-ingestion redesign). At 500 concurrent live runs with
    # 5 sessions per ``finalize_run``, a single worker needs ~40
    # connections in its pool to avoid pool-checkout queueing. The 8
    # Celery shards × 40 = 320 aggregate worker connections; PG
    # ``max_connections`` should be ≥ 500 with headroom. See
    # docs/SCALABLE_INGESTION_DESIGN.md § Phase 2.
    return {"development": 5, "staging": 15, "production": 40}.get(settings.APP_ENV, 5)


def _max_overflow() -> int:
    if settings.PG_MAX_OVERFLOW is not None:
        return settings.PG_MAX_OVERFLOW
    # Bumped 50 → 100 alongside the pool-size change so a burst can
    # temporarily exceed steady-state without ``QueuePool limit`` errors.
    return {"development": 10, "staging": 30, "production": 100}.get(settings.APP_ENV, 10)


# Phase 2.3 — minimum recommended pool sizing per process. A worker
# whose effective pool is smaller than this should log a warning at
# startup so the operator sees it before the system hits load. The
# values are derived from "5 sessions per finalize_run × ~10 concurrent
# finalizes per worker = 50 connections" — slightly below the
# production default so dev/staging don't false-alarm.
_RECOMMENDED_MIN_POOL_FOR_INGESTION_WORKERS = 30


def warn_if_pool_undersized_for_ingestion() -> None:
    """Emit a structured warning when the configured pool is too small
    for the Phase-2 ingestion targets. Called once at process startup
    by ``bootstrap.py`` so the warning lands in container logs at the
    moment the size mismatch matters."""
    import structlog as _sl
    log = _sl.get_logger("db.pool")
    effective = _pool_size() + _max_overflow()
    if effective < _RECOMMENDED_MIN_POOL_FOR_INGESTION_WORKERS:
        log.warning(
            "pg_pool_undersized_for_ingestion",
            effective_pool_capacity=effective,
            pool_size=_pool_size(),
            max_overflow=_max_overflow(),
            recommended_min=_RECOMMENDED_MIN_POOL_FOR_INGESTION_WORKERS,
            note=(
                "Under Phase 2 ingestion load (500 concurrent live runs), "
                "this pool will queue checkouts and slow finalize_run. "
                "Set PG_POOL_SIZE / PG_MAX_OVERFLOW env vars or run "
                "in production-mode for the auto-tuned defaults."
            ),
        )


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Build (or return the cached) async engine.

    P3-5: pool_pre_ping only in dev (saves 1 RTT per checkout in
    production), pool_recycle capped at 900s to stay within typical PG
    idle timeouts.
    """
    return create_async_engine(
        settings.DATABASE_URL,
        echo=settings.is_development,
        pool_pre_ping=settings.APP_ENV != "production",
        pool_size=_pool_size(),
        max_overflow=_max_overflow(),
        pool_recycle=min(settings.PG_POOL_RECYCLE, 900),
        pool_timeout=settings.PG_POOL_TIMEOUT,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Build (or return the cached) async session factory."""
    return async_sessionmaker(
        get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


class _SessionFactoryProxy:
    """Callable that resolves the CURRENT session factory on every call.

    Why this exists (F-027). ``__getattr__`` below runs **once** per importing
    module, so ``from app.db.postgres import AsyncSessionLocal`` at MODULE level
    binds whatever object it returned at first import — permanently, into that
    module's namespace.

    That is fine in the API process, whose engine is long-lived. It is a bug in
    the Celery worker: ``worker/tasks.py::_run_async`` disposes the engine and
    clears both ``@lru_cache``es at the end of every task, so from the second
    task onward a module-level binding still pointed at the factory of the
    **disposed** engine. Modules importing inside a function (``tasks.py``)
    re-resolved and got a fresh factory; modules importing at module level
    (``ingestion_pipeline.py``, ``ingestion.py``, and ~38 others) did not.

    The observable result was a 100%-reproducible failure at ``finalize_run``'s
    first query -- ``asyncpg InterfaceError: cannot perform operation: another
    operation is in progress`` -- 162 times across 32 bulk-ingest tasks, while
    the same tasks logged 32 clean engine builds and 32 successful disposes.
    Teardown was never the problem; a stale *reference* surviving it was.

    Returning a proxy instead of the factory makes the module-level binding
    stable and the resolution late, fixing every call site without editing any
    of them. Every usage in the codebase is a plain ``AsyncSessionLocal(...)``
    call, which is all this needs to support.
    """

    __slots__ = ()

    def __call__(self, *args: Any, **kwargs: Any) -> AsyncSession:
        return get_session_factory()(*args, **kwargs)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "<AsyncSessionLocal proxy -> app.db.postgres.get_session_factory()>"


_session_factory_proxy = _SessionFactoryProxy()


def __getattr__(name: str) -> Any:
    """Module-level lazy attribute access (PEP 562).

    Preserves ``from app.db.postgres import engine`` and
    ``from app.db.postgres import AsyncSessionLocal`` for existing
    callers — the engine is only constructed on first reference, not at
    import time. Importing this module without ``DATABASE_URL`` set is
    safe as long as no caller actually touches the engine.

    ``AsyncSessionLocal`` returns a *proxy* rather than the factory itself so
    that module-level importers cannot pin a stale factory across a worker's
    per-task engine disposal — see ``_SessionFactoryProxy``.
    """
    if name == "engine":
        return get_engine()
    if name == "AsyncSessionLocal":
        return _session_factory_proxy
    raise AttributeError(f"module 'app.db.postgres' has no attribute {name!r}")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that provides a database session."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create all tables on startup (development only). Use migrations in production."""
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose of the connection pool on shutdown."""
    await get_engine().dispose()


async def dispose_engine_for_loop() -> None:
    """Dispose the cached async engine *within the current event loop* and
    clear the lazy-build caches so the next caller rebuilds a fresh engine.

    Background (BUG-003): Celery tasks each run their coroutine in a private,
    short-lived event loop (``worker/tasks.py::_run_async`` →
    ``asyncio.new_event_loop()`` … ``loop.close()``). The ``@lru_cache``'d
    ``get_engine()`` builds the async engine — and its pooled asyncpg
    connections — bound to whichever loop was current on first use. When that
    loop is closed at the end of the task, the still-pooled connections remain
    attached to the now-dead loop. On the next task (or at GC) asyncpg tries to
    finalize/terminate those connections on the dead loop and raises
    ``RuntimeError: Event loop is closed`` ("Exception terminating
    connection …"), which surfaces in the AI pipeline as ``errors=1`` / status
    ``partial``.

    This helper must be awaited from inside the task's loop, in a ``finally``
    block, *before* the loop is closed. After disposing, both ``@lru_cache``'d
    builders are cleared so the next ``_run_async`` invocation constructs a new
    engine bound to its own fresh loop — mirroring the Redis-singleton reset
    already done in ``_run_async``.

    The request-path (FastAPI) engine is unaffected: the API process disposes
    via ``close_db()`` on shutdown and never closes the loop mid-process, so
    its long-lived engine keeps its pool. Only the worker calls this per task.
    """
    # ``get_engine`` may never have been built (a task that touched no DB).
    # Inspect the cache without forcing a build.
    if get_engine.cache_info().currsize:
        engine = get_engine()
        try:
            await engine.dispose()
        finally:
            get_session_factory.cache_clear()
            get_engine.cache_clear()
