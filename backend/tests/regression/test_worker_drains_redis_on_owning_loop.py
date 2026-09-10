"""Redis is drained on the loop that owns it -- once, not per task.

The defect (homelab, 2026-08-22). Each Celery task ran on a fresh loop and, in
teardown, drained the engine and the httpx client on it -- but not Redis.
``reset_loop_bound_clients()`` only NULLED ``_pool``/``_client``, at the start
of the NEXT task, abandoning a pool still holding sockets bound to a loop that
was already closed. One leaked pool per task: invisible at low volume, and a
burst of ~750 pipelines in one hour failed ~96% of summary stages with
``Event loop is closed``, against ~1.6% at normal rates.

Since re-audit M1 a worker child keeps one loop (``worker/loop_runner.py``) and
the Redis pool lives as long as it does. So the guard becomes:

* nothing is drained between ordinary tasks -- they share one pool, so none
  accumulates, which is the load-correlated part of the original defect;
* the pool is drained exactly once, on its own still-open loop, when that loop
  is torn down: at worker shutdown, or after an interrupted task;
* a failing drain never escapes teardown.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("asyncpg")

import app.db.redis_client as redis_client  # noqa: E402
from app.worker import loop_runner  # noqa: E402
from app.worker.tasks import _run_async  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_worker_loop():
    loop_runner.shutdown_worker_loop()
    yield
    loop_runner.shutdown_worker_loop()


@pytest.fixture
def spy_close(monkeypatch):
    """Record calls to close_redis and whether its loop was still open."""
    calls: list[bool] = []

    async def _fake_close():
        loop = asyncio.get_running_loop()
        calls.append(not loop.is_closed())

    monkeypatch.setattr(redis_client, "close_redis", _fake_close)
    # Neutralise the other drains so this test isolates Redis.
    import app.core.http_client as http
    import app.db.postgres as pg

    async def _noop():
        return None

    monkeypatch.setattr(pg, "dispose_engine_for_loop", _noop, raising=False)
    monkeypatch.setattr(http, "close_http_client", _noop, raising=False)
    return calls


def _interrupt():
    # Propagates out of the loop, as a signal-raised soft time limit does
    # when it lands while the loop is waiting.
    raise KeyboardInterrupt


def test_ordinary_tasks_share_the_pool_so_none_accumulates(spy_close):
    """N tasks, one pool, and no drain until the loop itself ends."""
    async def _work():
        return 1

    for _ in range(5):
        assert _run_async(_work()) == 1

    assert spy_close == []


def test_the_pool_is_drained_once_on_its_own_loop_at_shutdown(spy_close):
    async def _work():
        return "done"

    assert _run_async(_work()) == "done"
    loop_runner.shutdown_worker_loop()

    assert spy_close == [True], "close_redis must run exactly once, on a loop that is still open"


def test_a_failing_task_keeps_the_loop_and_its_pool(spy_close):
    async def _boom():
        raise RuntimeError("task failed")

    async def _work():
        return "next"

    with pytest.raises(RuntimeError, match="task failed"):
        _run_async(_boom())
    assert spy_close == [], "a task that merely failed tore the pool down"
    assert _run_async(_work()) == "next"


def test_an_interrupted_task_drains_and_discards_the_loop(spy_close):
    """The task is left pending and the loop cannot be trusted: tear it down."""
    loops: list[object] = []

    async def _interrupted():
        loops.append(asyncio.get_running_loop())
        asyncio.get_running_loop().call_soon(_interrupt)
        await asyncio.sleep(3600)

    async def _next():
        loops.append(asyncio.get_running_loop())
        return "fresh"

    with pytest.raises(KeyboardInterrupt):
        _run_async(_interrupted())
    assert spy_close == [True], "the interrupted loop was not drained on its own loop"

    assert _run_async(_next()) == "fresh"
    assert loops[0] is not loops[1], "the next task reused the interrupted loop"
    assert loops[0].is_closed()


def test_a_failing_drain_does_not_escape_teardown(monkeypatch):
    """Teardown must never turn worker shutdown into a crash."""
    async def _raising_close():
        raise RuntimeError("redis already gone")

    monkeypatch.setattr(redis_client, "close_redis", _raising_close)

    async def _work():
        return "survived"

    assert _run_async(_work()) == "survived"
    loop_runner.shutdown_worker_loop()  # must not raise


# ── The blanket handler must name what failed ───────────────────────────────


@pytest.mark.asyncio
async def test_summary_error_names_the_failing_operation(monkeypatch):
    """"Summary agent error: Event loop is closed" told an operator nothing.

    765 rows carrying that sentence accumulated on the homelab with no way to
    tell which await raised, because one handler wrapped the entire method.
    """
    from unittest.mock import AsyncMock

    from app.agents.summary_agent import SummaryAgent

    agent = SummaryAgent()
    agent.mark_stage_running = AsyncMock()
    agent.mark_stage_done = AsyncMock()
    agent.broadcast_progress = AsyncMock()
    agent.log_decision = AsyncMock()

    async def _boom(*_a, **_kw):
        raise RuntimeError("Event loop is closed")

    monkeypatch.setattr(agent, "_fetch_similar_failures", _boom)

    await agent.run({
        "pipeline_run_id": "p1", "test_run_id": "r1", "project_id": "proj",
        "test_run_data": {}, "analyses": {},
    })

    recorded = agent.mark_stage_done.call_args.kwargs.get("error") or ""
    assert "fetch_similar_failures" in recorded, (
        f"error must name the operation, got: {recorded!r}"
    )
    assert "RuntimeError" in recorded, "error must name the exception type"
