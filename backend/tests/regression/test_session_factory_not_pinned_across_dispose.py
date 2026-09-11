"""A module-level ``AsyncSessionLocal`` import must not pin a disposed engine.

**Root cause of F-027**, found by instrumenting the live worker rather than by
reading code — two earlier hypotheses had already been disproved by measurement.

What the instrumentation showed for one 60-run bulk ingest:

    F027_FORK_HANDLER_FIRED   4    (pids 7,8,9,10 — engine_cached=0 in each)
    F027_TASK_START          32
    F027_ENGINE_BUILD        32    (a fresh engine per task)
    F027_DISPOSE_OK          32
    F027_DISPOSE_FAILED       0
    "another operation is in progress"  162

So every task built its own engine and tore it down cleanly, and the error still
fired ~5x per task. That eliminated fork inheritance (nothing to inherit —
``engine_cached=0``), stale-loop teardown (dispose never failed), and cross-task
pool reuse. Every one of the 32 tracebacks was identical::

    tasks.py::ingest_uploaded_file -> _run_async -> _run
    ingestion_pipeline.py:347  finalize_run
    ingestion.py:750           _update_run_aggregates      <- always here

**The mechanism.** PEP 562 ``__getattr__`` runs *once per importing module*, so
``from app.db.postgres import AsyncSessionLocal`` at MODULE level permanently
binds whatever object it returned at first import.
``worker/tasks.py::_run_async`` then disposed the engine and cleared both
``@lru_cache``es after every task (since re-audit M1, only when a worker
loop is torn down). Modules importing **inside a function**
(``tasks.py``) re-resolved and got a fresh factory; modules importing at
**module level** (``ingestion_pipeline``, ``ingestion``, ~38 others) kept the
factory of the **disposed** engine — hence a deterministic failure on
``finalize_run``'s first query, from task #2 onward.

Teardown was never the problem. A stale *reference* surviving it was.

Fix: ``AsyncSessionLocal`` resolves to a callable proxy, so the module-level
binding is stable while resolution stays late. No call site changed.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from app.db import postgres  # noqa: E402
from app.db.postgres import AsyncSessionLocal  # noqa: E402  (module-level, on purpose)


def _reset_caches() -> None:
    """What the worker does to these caches when it tears its loop down."""
    postgres.get_session_factory.cache_clear()
    postgres.get_engine.cache_clear()


class TestTheModuleLevelBindingStaysCurrent:
    def test_binding_survives_a_cache_clear_and_uses_the_new_engine(self):
        """The regression, in one assertion.

        ``AsyncSessionLocal`` here was imported at module scope — the same shape
        ``ingestion_pipeline`` uses.
        """
        _reset_caches()
        first = AsyncSessionLocal()
        first_engine = first.bind

        _reset_caches()  # a task boundary
        second = AsyncSessionLocal()

        assert second.bind is not first_engine, (
            "the module-level import is pinned to the engine disposed at the end "
            "of the previous task — finalize_run would fail on its first query"
        )

    def test_it_matches_a_freshly_resolved_factory(self):
        _reset_caches()
        AsyncSessionLocal()
        _reset_caches()

        expected_engine = postgres.get_engine()
        assert AsyncSessionLocal().bind is expected_engine, (
            "the proxy did not resolve to the current engine"
        )

    def test_repeated_task_boundaries_keep_working(self):
        """Task #2 was where this bit; make sure #3 and #4 are fine too."""
        seen = []
        for _ in range(4):
            _reset_caches()
            seen.append(AsyncSessionLocal().bind)
        assert len(set(map(id, seen))) == 4, (
            "engines were reused across simulated task boundaries"
        )


class TestTheProxyContract:
    def test_async_session_local_is_callable(self):
        assert callable(AsyncSessionLocal)

    def test_calling_it_returns_a_session(self):
        from sqlalchemy.ext.asyncio import AsyncSession

        assert isinstance(AsyncSessionLocal(), AsyncSession)

    def test_it_is_not_the_factory_itself(self):
        """If it were, module-level importers would pin it again."""
        _reset_caches()
        assert AsyncSessionLocal is not postgres.get_session_factory()

    def test_the_binding_object_is_stable_across_clears(self):
        """Stable identity is what makes the module-level import safe."""
        before = postgres.AsyncSessionLocal
        _reset_caches()
        assert postgres.AsyncSessionLocal is before


class TestEngineAccessorUnchanged:
    def test_engine_attribute_still_resolves_eagerly(self):
        """``engine`` intentionally still returns the engine, not a proxy —
        nothing binds it at module level in a worker-executed path."""
        _reset_caches()
        assert postgres.engine is postgres.get_engine()

    def test_unknown_attributes_still_raise(self):
        with pytest.raises(AttributeError):
            postgres.definitely_not_a_real_attribute
