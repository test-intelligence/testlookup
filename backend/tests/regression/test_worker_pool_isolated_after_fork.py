"""Each prefork child must build its own DB engine, not inherit the parent's.

Found by exploratory testing (2026-08-07). A 60-run bulk upload had most first
attempts fail in the ingestion worker with::

    sqlalchemy.exc.InterfaceError:
      asyncpg.exceptions._base.InterfaceError:
      cannot perform operation: another operation is in progress
    [SQL: SELECT count(test_cases.id) ... WHERE test_cases.test_run_id = $5]

That message means one connection was driven by two coroutines at once.

Mechanism: Celery's default pool is **prefork** — the ingestion worker runs
``--concurrency=4``, i.e. four children forked from one parent.
``get_engine()`` / ``get_session_factory()`` are ``@lru_cache``'d, so if anything
touches the database in the parent before the fork, every child inherits the same
SQLAlchemy pool and therefore the same open asyncpg **sockets (file
descriptors)**. Two children using one socket produces exactly this error.

``worker/tasks.py::_run_async`` already solves the *event loop* half of this
(BUG-003: a pool bound to a loop that was later closed). It cannot help here —
that is per-process bookkeeping, whereas this is one pool shared ACROSS processes
by ``fork()``.

The handler must **clear** the caches, never ``dispose()`` them: disposing inside
a child would close sockets the parent and sibling children still hold. Clearing
means the child's next ``get_engine()`` builds a fresh engine and the inherited
one is simply never used here again. That distinction is the whole point, so it
is asserted below.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("celery")
pytest.importorskip("sqlalchemy")

from app.worker import celery_app as celery_mod  # noqa: E402


def _handler():
    fn = getattr(celery_mod, "_reset_db_pool_after_fork", None)
    assert fn is not None, (
        "no post-fork handler: prefork children inherit the parent's asyncpg "
        "sockets and collide with 'another operation is in progress'"
    )
    return fn


class TestHandlerExists:
    def test_a_post_fork_handler_is_defined(self):
        assert callable(_handler())

    def test_it_is_wired_to_worker_process_init(self):
        src = inspect.getsource(celery_mod)
        assert "worker_process_init" in src, (
            "the reset is never connected to Celery's post-fork signal, so it "
            "never runs in the children"
        )
        assert "@worker_process_init.connect" in src

    def test_it_accepts_arbitrary_kwargs(self):
        """Celery passes signal kwargs; a strict signature would raise at fork."""
        sig = inspect.signature(_handler())
        assert any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ), "handler must accept **kwargs — Celery sends signal metadata"


class TestItClearsRatherThanDisposes:
    """The safety property. Disposing in a child closes shared file descriptors."""

    def test_both_caches_are_cleared(self):
        src = inspect.getsource(_handler())
        assert "get_engine.cache_clear()" in src
        assert "get_session_factory.cache_clear()" in src, (
            "clearing only the engine leaves the session factory bound to the "
            "inherited engine, so children keep using the parent's pool"
        )

    def test_it_does_not_dispose(self):
        src = inspect.getsource(_handler())
        assert "dispose" not in src, (
            "dispose() inside a forked child closes sockets the parent and "
            "sibling children are still using — clear the cache instead"
        )


class TestTheAccessorsSupportThis:
    """The handler relies on both accessors being lru_cache'd."""

    def test_engine_accessor_is_cached_and_clearable(self):
        from app.db.postgres import get_engine

        assert hasattr(get_engine, "cache_clear"), (
            "get_engine is no longer @lru_cache'd — the post-fork reset cannot work"
        )

    def test_session_factory_accessor_is_cached_and_clearable(self):
        from app.db.postgres import get_session_factory

        assert hasattr(get_session_factory, "cache_clear")

    def test_clearing_yields_a_distinct_engine(self):
        """Proves the reset actually produces a new object, not the same one."""
        from app.db import postgres

        first = postgres.get_engine()
        postgres.get_engine.cache_clear()
        second = postgres.get_engine()
        assert first is not second, (
            "cache_clear() did not produce a fresh engine; children would keep "
            "the inherited pool"
        )
