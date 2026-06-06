"""Regression (BUG-003): "Event loop is closed" raised when an asyncpg
connection is torn down in the Celery workers.

Symptom (live homelab run 493d5c1f, 2026-06-06): the AI / ingestion
workers logged ``Exception terminating connection <AdaptedConnection
<asyncpg...Connection...>>`` → ``RuntimeError: Event loop is closed`` and
the AI pipeline ended with ``errors=1`` / status ``partial`` (which then
hides it on /agents).

Root cause: every Celery task runs its coroutine in a private, short-lived
event loop (``worker/tasks.py::_run_async`` → ``asyncio.new_event_loop()``
… ``loop.close()``). The ``@lru_cache``'d ``app.db.postgres.get_engine()``
builds the async engine — and pools its asyncpg connections — bound to the
loop that was current on first use. When that loop is closed at the end of
the task, the still-pooled connections remain attached to a *dead* loop; the
next task (or GC) then tries to finalize them on that dead loop and raises
``RuntimeError: Event loop is closed``.

Fix: ``_run_async`` now disposes the engine on its own loop, in the
``finally`` block, *before* the loop closes (``dispose_engine_for_loop()``),
and clears the lazy-build caches so the next task rebuilds a fresh engine on
its own loop — mirroring the Redis-singleton reset already done there.

These pins assert the dispose-in-finally wiring directly (a true asyncpg
repro needs a live Postgres), plus an end-to-end check that no
"Event loop is closed" escapes ``_run_async``.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# app.worker.tasks pulls in the real celery package, which isn't installed in
# the local dev venv (it is in CI / the container). Skip the _run_async wiring
# checks gracefully when it's absent; the dispose_engine_for_loop checks below
# have no such dependency and always run.
try:  # pragma: no cover - import guard
    from app.worker import tasks as _tasks  # noqa: F401
    _HAS_CELERY = True
except Exception:  # pragma: no cover
    _HAS_CELERY = False

_needs_celery = pytest.mark.skipif(
    not _HAS_CELERY, reason="celery not installed locally (runs in CI/container)"
)


# ---------------------------------------------------------------------------
# dispose_engine_for_loop()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispose_engine_for_loop_disposes_and_clears_caches(monkeypatch):
    """When an engine has been built, dispose_engine_for_loop must call
    engine.dispose() and clear BOTH lazy-build lru_caches so the next caller
    rebuilds on a fresh loop."""
    import app.db.postgres as pg

    # ``AsyncEngine.dispose`` is read-only on the instance, so we can't build a
    # real engine and monkeypatch its method. Instead patch the cached builder
    # to hand back a fake engine and verify dispose_engine_for_loop awaits it
    # and clears the caches. We make cache_info() report a built engine so the
    # "engine exists" branch is taken.
    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()

    pg.get_engine.cache_clear()
    pg.get_session_factory.cache_clear()
    session_factory_cleared = {"called": False}

    def _fake_get_engine():
        return fake_engine

    _fake_get_engine.cache_info = lambda: type("CI", (), {"currsize": 1})()
    _fake_get_engine.cache_clear = MagicMock()

    def _factory_cache_clear():
        session_factory_cleared["called"] = True

    monkeypatch.setattr(pg, "get_engine", _fake_get_engine)
    monkeypatch.setattr(pg.get_session_factory, "cache_clear", _factory_cache_clear)

    await pg.dispose_engine_for_loop()

    fake_engine.dispose.assert_awaited_once()
    _fake_get_engine.cache_clear.assert_called_once()
    assert session_factory_cleared["called"] is True


@pytest.mark.asyncio
async def test_dispose_engine_for_loop_real_engine_clears_caches(monkeypatch):
    """Integration-flavoured variant against a REAL (non-connecting) engine:
    after dispose, both lru_caches are empty so the next task rebuilds fresh.

    Uses a non-connecting asyncpg URL — ``create_async_engine`` never opens a
    socket, and ``dispose()`` on an unused pool makes no network call — so this
    is safe with no live Postgres."""
    import app.db.postgres as pg

    monkeypatch.setattr(
        pg.settings, "DATABASE_URL",
        "postgresql+asyncpg://test:test@localhost:5432/test",
        raising=False,
    )
    pg.get_engine.cache_clear()
    pg.get_session_factory.cache_clear()

    # Force-build the engine + factory (simulating a task that touched the DB).
    pg.get_engine()
    pg.get_session_factory()
    assert pg.get_engine.cache_info().currsize == 1
    assert pg.get_session_factory.cache_info().currsize == 1

    await pg.dispose_engine_for_loop()

    # Caches cleared → next get_engine() builds a brand-new engine.
    assert pg.get_engine.cache_info().currsize == 0
    assert pg.get_session_factory.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_dispose_engine_for_loop_noop_when_engine_never_built(monkeypatch):
    """If no task touched the DB, the engine was never built — dispose must
    NOT force-build one just to dispose it (that would open a pool only to
    immediately tear it down, and could fail with no DATABASE_URL)."""
    import app.db.postgres as pg

    pg.get_engine.cache_clear()
    pg.get_session_factory.cache_clear()
    assert pg.get_engine.cache_info().currsize == 0

    with patch.object(pg, "get_engine", wraps=pg.get_engine) as spy:
        await pg.dispose_engine_for_loop()
        # currsize==0 path: we only read cache_info(), never invoke the builder.
        spy.assert_not_called()


# ---------------------------------------------------------------------------
# _run_async wiring
# ---------------------------------------------------------------------------


@_needs_celery
def test_run_async_disposes_engine_before_loop_close():
    """The tasks._run_async finally-block must await dispose_engine_for_loop()
    on the SAME loop, while that loop is still open (not closed)."""
    from app.worker import tasks

    observed: dict[str, object] = {}

    async def _fake_dispose() -> None:
        loop = asyncio.get_event_loop()
        observed["dispose_loop"] = loop
        observed["loop_running_at_dispose"] = loop.is_running()
        observed["loop_closed_at_dispose"] = loop.is_closed()

    async def _body() -> str:
        observed["body_loop"] = asyncio.get_event_loop()
        return "ok"

    with patch("app.db.postgres.dispose_engine_for_loop", _fake_dispose):
        result = tasks._run_async(_body())

    assert result == "ok"
    # dispose ran...
    assert "dispose_loop" in observed, "dispose_engine_for_loop was never awaited"
    # ...on the same loop the coroutine body ran on...
    assert observed["dispose_loop"] is observed["body_loop"]
    # ...and BEFORE that loop was closed.
    assert observed["loop_closed_at_dispose"] is False


@_needs_celery
def test_run_async_no_event_loop_closed_error_on_repeated_calls():
    """End-to-end: invoking _run_async repeatedly (as the worker does, one
    loop per task) must not surface a 'Event loop is closed' RuntimeError.

    Before the fix the pooled engine from call N stayed bound to call N's
    closed loop and blew up; after the fix each call disposes its own engine
    in-loop. We stub the engine with a dispose() that records which loop it
    ran on to prove cross-loop teardown never happens."""
    from app.worker import tasks

    dispose_loops: list[object] = []

    async def _fake_dispose() -> None:
        dispose_loops.append(asyncio.get_event_loop())

    async def _touch_db() -> int:
        # Simulate a task that "used" the DB.
        await asyncio.sleep(0)
        return 1

    with patch("app.db.postgres.dispose_engine_for_loop", _fake_dispose):
        for _ in range(3):
            # Must not raise RuntimeError("Event loop is closed").
            assert tasks._run_async(_touch_db()) == 1

    # Three task invocations → three distinct loops, each disposed in-loop.
    assert len(dispose_loops) == 3
    assert len(set(id(loop) for loop in dispose_loops)) == 3
    # Every dispose ran on a loop that was NOT yet closed.
    assert all(not loop.is_closed() for loop in dispose_loops)
