"""Regression guard: a Celery task must drain Redis on the loop that owns it.

The defect
----------
``_run_async`` builds a fresh event loop per Celery task and, in teardown,
drains the SQLAlchemy engine and the shared httpx client *on that loop* --
because both hold sockets bound to it. Redis was left out.

``reset_loop_bound_clients()`` only NULLS ``_pool`` / ``_client``, and it runs
at the START of the *next* task, by which point the loop those sockets belong
to is already closed. ``close_redis()`` existed all along and was called from
the FastAPI lifespan and from nowhere in the worker path. Its own docstring
described the clients here as ones that "only need dropping, not draining",
which is the assumption that let the leak through.

Because the leak is one pool per task, it is invisible at low volume and severe
under load. Measured on the homelab 2026-08-22: a burst of ~750 pipelines in a
single hour failed ~96% of summary stages with ``Event loop is closed``, against
~1.6% at normal rates.

What is guarded
---------------
* the task teardown actually calls ``close_redis`` -- on the task's own loop,
  before it closes;
* it runs even when the task body raises, since a failing task leaks exactly
  like a succeeding one;
* a failure inside the drain never escapes teardown, matching how the engine
  and httpx drains already behave.
"""
from __future__ import annotations

import pytest

pytest.importorskip("asyncpg")

import app.db.redis_client as redis_client  # noqa: E402
from app.worker.tasks import _run_async  # noqa: E402


@pytest.fixture
def spy_close(monkeypatch):
    """Record calls to close_redis and whether a live loop was running."""
    calls: list[bool] = []

    async def _fake_close():
        import asyncio
        loop = asyncio.get_running_loop()
        calls.append(not loop.is_closed())

    monkeypatch.setattr(redis_client, "close_redis", _fake_close)
    # Neutralise the other drains so this test isolates Redis.
    import app.db.postgres as pg
    import app.core.http_client as http

    async def _noop():
        return None

    monkeypatch.setattr(pg, "dispose_engine_for_loop", _noop, raising=False)
    monkeypatch.setattr(http, "close_http_client", _noop, raising=False)
    return calls


def test_redis_is_drained_on_the_tasks_own_loop(spy_close):
    async def _work():
        return "done"

    assert _run_async(_work()) == "done"

    assert spy_close == [True], (
        "close_redis must run exactly once, on a loop that is still open"
    )


def test_redis_is_drained_even_when_the_task_raises(spy_close):
    async def _boom():
        raise RuntimeError("task failed")

    with pytest.raises(RuntimeError, match="task failed"):
        _run_async(_boom())

    # A failing task abandons a pool exactly like a successful one.
    assert spy_close == [True]


def test_every_task_drains_so_pools_do_not_accumulate(spy_close):
    """The load-correlated part: N tasks must produce N drains, not one."""
    async def _work():
        return 1

    for _ in range(5):
        _run_async(_work())

    assert spy_close == [True] * 5


def test_a_failing_drain_does_not_escape_teardown(monkeypatch):
    """Teardown must never turn a successful task into a failed one."""
    async def _raising_close():
        raise RuntimeError("redis already gone")

    monkeypatch.setattr(redis_client, "close_redis", _raising_close)

    async def _work():
        return "survived"

    assert _run_async(_work()) == "survived"


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

    # Fail at a known operation.
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
