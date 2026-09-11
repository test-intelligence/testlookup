"""Regression (BUG-003, re-audit M1): the async engine and the worker's event loop.

BUG-003 (homelab run 493d5c1f, 2026-06-06). The AI and ingestion workers logged
``Exception terminating connection <AdaptedConnection <asyncpg...>>`` then
``RuntimeError: Event loop is closed``, and the AI pipeline ended ``partial``.
Every task ran on a private, short-lived loop; the ``@lru_cache``'d engine
pooled asyncpg connections bound to the first loop that used it, and once that
loop closed, finalising those connections on it raised.

The fix then was to dispose the engine on the task's own loop, before closing
it, after every task. Correct for a loop per task -- and the reason no
connection ever outlived a task (re-audit M1).

Now a worker child keeps one loop (``worker/loop_runner.py``) and the engine
lives as long as the loop. BUG-003's rule still holds one level up: the engine
is disposed on the loop that owns it, before that loop closes -- at worker
shutdown, or when an interrupted task discards the loop -- and never after an
ordinary task.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# app.worker.tasks pulls in the real celery package. Skip the _run_async checks
# gracefully when it is absent; the dispose_engine_for_loop checks below have
# no such dependency and always run.
try:  # pragma: no cover - import guard
    from app.worker import tasks as _tasks  # noqa: F401
    _HAS_CELERY = True
except Exception:  # pragma: no cover
    _HAS_CELERY = False

_needs_celery = pytest.mark.skipif(
    not _HAS_CELERY, reason="celery not installed locally (runs in CI/container)"
)


@pytest.fixture(autouse=True)
def _no_leftover_worker_loop():
    yield
    from app.worker import loop_runner

    loop_runner.shutdown_worker_loop()


# ---------------------------------------------------------------------------
# dispose_engine_for_loop()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispose_engine_for_loop_disposes_and_clears_caches(monkeypatch):
    """When an engine has been built, dispose_engine_for_loop must call
    engine.dispose() and clear BOTH lazy-build lru_caches so the next caller
    rebuilds on a fresh loop."""
    import app.db.postgres as pg

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
    """Against a REAL (non-connecting) engine: after dispose, both lru_caches are
    empty so the next caller rebuilds fresh. ``create_async_engine`` never opens
    a socket, and ``dispose()`` on an unused pool makes no network call."""
    import app.db.postgres as pg

    monkeypatch.setattr(
        pg.settings, "DATABASE_URL",
        "postgresql+asyncpg://test:test@localhost:5432/test",
        raising=False,
    )
    pg.get_engine.cache_clear()
    pg.get_session_factory.cache_clear()

    pg.get_engine()
    pg.get_session_factory()
    assert pg.get_engine.cache_info().currsize == 1
    assert pg.get_session_factory.cache_info().currsize == 1

    await pg.dispose_engine_for_loop()

    assert pg.get_engine.cache_info().currsize == 0
    assert pg.get_session_factory.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_dispose_engine_for_loop_noop_when_engine_never_built(monkeypatch):
    """If nothing touched the DB, dispose must NOT force-build an engine just to
    dispose it."""
    import app.db.postgres as pg

    pg.get_engine.cache_clear()
    pg.get_session_factory.cache_clear()
    assert pg.get_engine.cache_info().currsize == 0

    with patch.object(pg, "get_engine", wraps=pg.get_engine) as spy:
        await pg.dispose_engine_for_loop()
        spy.assert_not_called()


# ---------------------------------------------------------------------------
# _run_async: one loop per worker child (re-audit M1)
# ---------------------------------------------------------------------------


@_needs_celery
def test_the_engine_is_not_disposed_between_tasks():
    """Three tasks, one loop, one engine: no per-task teardown, and no
    'Event loop is closed' from reusing it."""
    from app.worker import tasks

    disposed: list[object] = []
    loops: list[object] = []

    async def _fake_dispose() -> None:
        disposed.append(asyncio.get_running_loop())

    async def _body() -> int:
        loops.append(asyncio.get_running_loop())
        await asyncio.sleep(0)
        return 1

    with patch("app.db.postgres.dispose_engine_for_loop", _fake_dispose):
        for _ in range(3):
            assert tasks._run_async(_body()) == 1

    assert disposed == [], (
        "the engine was torn down after an ordinary task, so its pool never "
        "outlives one (re-audit M1)"
    )
    assert len(set(map(id, loops))) == 1, "each task got its own loop again"


@_needs_celery
def test_shutdown_disposes_the_engine_on_its_own_loop_before_closing_it():
    """BUG-003's rule, at the one place teardown now happens."""
    from app.worker import loop_runner, tasks

    observed: dict[str, object] = {}

    async def _fake_dispose() -> None:
        loop = asyncio.get_running_loop()
        observed["loop"] = loop
        observed["closed"] = loop.is_closed()

    async def _body() -> str:
        observed["body_loop"] = asyncio.get_running_loop()
        return "ok"

    with patch("app.db.postgres.dispose_engine_for_loop", _fake_dispose):
        assert tasks._run_async(_body()) == "ok"
        assert "loop" not in observed, "disposed before the worker shut down"
        loop_runner.shutdown_worker_loop()

    assert observed["loop"] is observed["body_loop"], "disposed on a different loop"
    assert observed["closed"] is False, "disposed after its loop was closed"
    assert observed["body_loop"].is_closed(), "shutdown left the loop open"
