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

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


def _pool_size() -> int:
    if settings.PG_POOL_SIZE is not None:
        return settings.PG_POOL_SIZE
    # A pool is created per Gunicorn/Celery child, so a large process-local
    # default multiplies into a connection storm during HPA scale-out. Keep
    # the safe fallback small; deployment manifests set this explicitly.
    return {"development": 5, "staging": 15, "production": 2}.get(settings.APP_ENV, 5)


def _max_overflow() -> int:
    if settings.PG_MAX_OVERFLOW is not None:
        return settings.PG_MAX_OVERFLOW
    return {"development": 10, "staging": 30, "production": 1}.get(settings.APP_ENV, 10)


def get_effective_pool_config() -> dict[str, int]:
    """Return the concrete pool values used to construct the SQLAlchemy engine."""
    return {
        "pool_size": _pool_size(),
        "max_overflow": _max_overflow(),
        "pool_recycle": min(settings.PG_POOL_RECYCLE, 900),
    }


def evaluate_server_connection_budget(
    *, server_max: int, superuser_reserved: int, reserved: int, role_limit: int
) -> dict[str, int]:
    """Compare measured PostgreSQL limits with the rendered fleet contract."""
    server_usable = (
        server_max
        - superuser_reserved
        - reserved
        - settings.PG_FLEET_OPERATIONAL_RESERVE
    )
    usable = min(server_usable, role_limit) if role_limit >= 0 else server_usable
    required = settings.PG_FLEET_REQUIRED_CONNECTIONS
    errors = []
    if server_max < settings.PG_FLEET_MAX_CONNECTIONS:
        errors.append(
            f"server max_connections={server_max} is below declared "
            f"PG_FLEET_MAX_CONNECTIONS={settings.PG_FLEET_MAX_CONNECTIONS}"
        )
    if required > usable:
        errors.append(
            f"fleet requires {required} connections but actual usable capacity is {usable} "
            f"after {superuser_reserved} superuser, {reserved} reserved, and "
            f"{settings.PG_FLEET_OPERATIONAL_RESERVE} operational reserved slots"
        )
    if errors:
        raise RuntimeError("PostgreSQL connection budget rejected: " + "; ".join(errors))
    return {
        "server_max": server_max,
        "superuser_reserved": superuser_reserved,
        "reserved": reserved,
        "role_limit": role_limit,
        "usable": usable,
        "required": required,
    }


async def verify_server_connection_budget() -> dict[str, int]:
    """Fail production startup when the real PostgreSQL capacity is too small.

    Static manifest validation proves the declared topology. This query closes
    the other half of the contract for managed databases, where a ConfigMap
    cannot prove the server's actual ``max_connections`` or role limit.
    """
    async with get_engine().connect() as connection:
        server_max = int((await connection.execute(text("SHOW max_connections"))).scalar_one())
        superuser_reserved = int(
            (await connection.execute(text("SHOW superuser_reserved_connections"))).scalar_one()
        )
        reserved = int(
            (
                await connection.execute(
                    text("SELECT COALESCE(current_setting('reserved_connections', true), '0')")
                )
            ).scalar_one()
        )
        role_limit = int(
            (
                await connection.execute(
                    text("SELECT rolconnlimit FROM pg_roles WHERE rolname = current_user")
                )
            ).scalar_one()
        )
    return evaluate_server_connection_budget(
        server_max=server_max,
        superuser_reserved=superuser_reserved,
        reserved=reserved,
        role_limit=role_limit,
    )


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Build (or return the cached) async engine.

    P3-5: pool_pre_ping only in dev (saves 1 RTT per checkout in
    production), pool_recycle capped at 900s to stay within typical PG
    idle timeouts.
    """
    pool = get_effective_pool_config()
    return create_async_engine(
        settings.DATABASE_URL,
        echo=settings.is_development,
        # Re-audit M1: a worker's pooled connections now outlive a task, so a
        # Postgres restart or an idle timeout must not strand dead ones in the
        # pool. The API keeps the P3-5 saving; its pool was always long-lived.
        pool_pre_ping=(
            settings.APP_ENV != "production" or settings.PG_PROCESS_ROLE == "worker"
        ),
        pool_size=pool["pool_size"],
        max_overflow=pool["max_overflow"],
        pool_recycle=pool["pool_recycle"],
        pool_timeout=settings.PG_POOL_TIMEOUT,
        # Re-audit H3 review: a DBAPIError renders the statement's bound
        # parameters into its own text -- "[parameters: (...)]" -- and
        # tracebacks are logged as text. Redaction recognises a secret by a
        # marker, and a bare positional value has none, so a password hash
        # or a token hash in an INSERT or UPDATE reached the log. Production
        # hides them; elsewhere they are how a failing statement is debugged.
        hide_parameters=settings.APP_ENV == "production",
        connect_args={
            "server_settings": {
                "application_name": f"testlookup-{settings.PG_PROCESS_ROLE}",
            }
        },
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

    That is fine in the API process, whose engine is long-lived. It was a bug
    in the Celery worker, whose task wrapper disposed the engine and cleared
    both ``@lru_cache``es at the end of every task, so from the second
    task onward a module-level binding still pointed at the factory of the
    **disposed** engine. Modules importing inside a function (``tasks.py``)
    re-resolved and got a fresh factory; modules importing at module level
    (``ingestion_pipeline.py``, ``ingestion.py``, and ~38 others) did not.
    Since re-audit M1 a worker keeps its engine across tasks, but it still
    disposes it when an interrupted task discards the loop, and clears the
    caches after fork, so the proxy still matters.

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
            from app.services.test_management_metrics_service import (
                emit_staged_test_management_metrics,
            )

            await emit_staged_test_management_metrics(session)
        except Exception:
            await session.rollback()
            from app.services.test_management_metrics_service import (
                discard_staged_test_management_metrics,
            )

            discard_staged_test_management_metrics(session)
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

    Background (BUG-003): Celery tasks used to run their coroutine in a
    private, short-lived event loop (``asyncio.new_event_loop()`` ...
    ``loop.close()``). The ``@lru_cache``'d ``get_engine()`` builds the async
    engine -- and its pooled asyncpg connections -- bound to whichever loop was
    current on first use. When that loop was closed at the end of the task, the
    still-pooled connections stayed attached to the dead loop, and asyncpg
    raised ``RuntimeError: Event loop is closed`` ("Exception terminating
    connection ...") when it later finalized them. That surfaced in the AI
    pipeline as ``errors=1`` / status ``partial``.

    A worker child now keeps one loop for its lifetime (``worker/loop_runner.py``,
    re-audit M1), and the rule holds one level up: await this on the loop that
    owns the engine, *before* that loop is closed -- at worker shutdown, or when
    an interrupted task discards the loop. Both ``@lru_cache``'d builders are
    then cleared, so the next loop builds its own engine, as
    ``reset_loop_bound_clients`` does for Redis and Mongo.

    The request-path (FastAPI) engine is unaffected: the API process disposes
    via ``close_db()`` on shutdown and never closes the loop mid-process, so
    its long-lived engine keeps its pool.
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
