"""Unit tests for the Phase 3 AI pipeline debouncer.

The debouncer turns ``close_session → run_agent_pipeline.apply_async``
(per-run, immediate) into ``close_session → SortedSet.zadd`` followed
by a beat-driven ``flush_pending`` that groups by project, applies
the daily LLM cost-budget cap, and fans out pipeline tasks.

Pins:

  1. ``enqueue_pipeline_for_run`` writes to the SortedSet with a
     parseable member encoding the run + project metadata.
  2. ``flush_pending`` ignores runs younger than the debounce window
     (so a burst of 10 events in 10s coalesces into one flush tick).
  3. Members older than the window are grouped by project and one
     pipeline task fires per run.
  4. Budget gate: when ``check_and_apply_cap`` returns ``block=True``,
     no pipeline tasks fire for that project. ``zrem`` still removes
     the blocked members so they don't pile up tick-after-tick.
  5. Budget gate: when the decision carries a ``mode_override``, the
     fanned-out task receives the override in its headers.
  6. ``AI_PIPELINE_DEBOUNCE_ENABLED=False`` falls back to direct
     dispatch (legacy path).
  7. Redis failure in ``enqueue`` falls back to direct dispatch so a
     Redis hiccup never loses a pipeline trigger.
"""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _stub_llm_cost_budget_module(*, block=False, mode_override=None, action="UNLIMITED"):
    """Replace ``app.services.llm_cost_budget`` in ``sys.modules`` with
    a stub that returns a fixed CapDecision-shaped object. Necessary
    because the real module imports ``AsyncSessionLocal`` at top level,
    which triggers lazy engine construction against an empty
    ``DATABASE_URL`` and crashes the test environment. The stub only
    exposes the attribute the debouncer touches (``check_and_apply_cap``)."""
    stub = ModuleType("app.services.llm_cost_budget")
    stub.check_and_apply_cap = AsyncMock(  # type: ignore[attr-defined]
        return_value=_fake_cap_decision(
            block=block, mode_override=mode_override, action=action,
        ),
    )
    return stub


def _fake_cap_decision(*, block=False, mode_override=None, action="UNLIMITED"):
    return SimpleNamespace(
        block=block,
        mode_override=mode_override,
        action=action,
        rationale="test",
        utilization_pct=0.0,
    )


def _fake_redis():
    """AsyncMock Redis stub with the methods the debouncer touches."""
    return SimpleNamespace(
        zadd=AsyncMock(return_value=1),
        zrangebyscore=AsyncMock(return_value=[]),
        zrem=AsyncMock(return_value=0),
        zcard=AsyncMock(return_value=0),
        set=AsyncMock(return_value=True),
    )


@pytest.fixture
def enable_debouncer(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "AI_PIPELINE_DEBOUNCE_ENABLED", True)


@pytest.mark.asyncio
async def test_enqueue_writes_to_sortedset(enable_debouncer):
    from app.services.ai_pipeline_debouncer import enqueue_pipeline_for_run

    redis = _fake_redis()
    with patch("app.db.redis_client.get_redis", return_value=redis):
        status = await enqueue_pipeline_for_run(
            project_id="proj-1", test_run_id="run-1",
            build_number="42", workflow_type="offline",
        )

    assert status == "debounced"
    redis.zadd.assert_awaited_once()
    # First positional arg is the key, second is the {member: score} dict.
    args, _ = redis.zadd.call_args
    assert args[0] == "testlookup:ai_pipeline_debounce"
    member = next(iter(args[1].keys()))
    # The serialized member must encode all four fields so the flush
    # task can reconstruct the run identity.
    assert member.split("|") == ["proj-1", "run-1", "42", "offline"]


@pytest.mark.asyncio
async def test_enqueue_falls_back_to_direct_when_redis_down(enable_debouncer):
    """A Redis hiccup must never lose a pipeline trigger — the run
    falls back to the legacy direct ``apply_async`` path."""
    from app.services.ai_pipeline_debouncer import enqueue_pipeline_for_run

    broken_redis = SimpleNamespace(zadd=AsyncMock(side_effect=ConnectionError("redis down")))
    fake_task = MagicMock()
    fake_task.apply_async = MagicMock()

    with patch("app.db.redis_client.get_redis", return_value=broken_redis), \
         patch("app.worker.tasks.run_agent_pipeline", fake_task):
        status = await enqueue_pipeline_for_run(
            project_id="proj-1", test_run_id="run-1", build_number="42",
        )

    assert status == "fallback_direct"
    fake_task.apply_async.assert_called_once()


@pytest.mark.asyncio
async def test_enqueue_uses_direct_when_debouncer_disabled(monkeypatch):
    """``AI_PIPELINE_DEBOUNCE_ENABLED=False`` reverts to immediate
    dispatch — used by tests and any deploy without the beat schedule."""
    from app.core import config
    monkeypatch.setattr(config.settings, "AI_PIPELINE_DEBOUNCE_ENABLED", False)
    from app.services.ai_pipeline_debouncer import enqueue_pipeline_for_run

    fake_task = MagicMock()
    fake_task.apply_async = MagicMock()
    with patch("app.worker.tasks.run_agent_pipeline", fake_task):
        status = await enqueue_pipeline_for_run(
            project_id="proj-1", test_run_id="run-1", build_number="42",
        )

    assert status == "direct"
    fake_task.apply_async.assert_called_once()


@pytest.mark.asyncio
async def test_flush_pending_no_eligible_members(enable_debouncer):
    """Empty queue → zero drained, no errors, no side effects."""
    from app.services.ai_pipeline_debouncer import flush_pending

    redis = _fake_redis()
    with patch("app.db.redis_client.get_redis", return_value=redis):
        summary = await flush_pending(now=1000.0)

    assert summary == {"drained": 0}
    redis.zrem.assert_not_awaited()


@pytest.mark.asyncio
async def test_flush_pending_fans_out_pipelines_per_run(enable_debouncer):
    """3 eligible runs in one project → 3 pipeline tasks fired,
    SortedSet cleaned up, per-project summary recorded."""
    from app.services.ai_pipeline_debouncer import flush_pending

    members = [
        "proj-1|run-A|42|offline",
        "proj-1|run-B|43|offline",
        "proj-1|run-C|44|offline",
    ]
    redis = _fake_redis()
    redis.zrangebyscore = AsyncMock(return_value=members)
    fake_task = MagicMock()
    fake_task.apply_async = MagicMock()

    stub = _stub_llm_cost_budget_module()
    with patch("app.db.redis_client.get_redis", return_value=redis), \
         patch("app.worker.tasks.run_agent_pipeline", fake_task), \
         patch.dict(sys.modules, {"app.services.llm_cost_budget": stub}):
        summary = await flush_pending(now=1000.0)

    assert summary["drained"] == 3
    assert summary["fired"] == 3
    assert summary["blocked"] == 0
    assert fake_task.apply_async.call_count == 3
    # Members removed from the SortedSet in a single ZREM call (variadic).
    redis.zrem.assert_awaited_once()


@pytest.mark.asyncio
async def test_flush_pending_respects_hard_block_budget(enable_debouncer):
    """When the cost-budget says ``block=True``, the project's queued
    runs are dropped (no pipeline tasks fire) but they ARE removed
    from the SortedSet so they don't pile up tick-after-tick.
    Hard-cap is a daily reset — re-queuing would just block again
    on the next flush, producing log churn without progress."""
    from app.services.ai_pipeline_debouncer import flush_pending

    members = ["proj-blocked|run-A|42|offline", "proj-blocked|run-B|43|offline"]
    redis = _fake_redis()
    redis.zrangebyscore = AsyncMock(return_value=members)
    fake_task = MagicMock()
    fake_task.apply_async = MagicMock()

    blocked_stub = _stub_llm_cost_budget_module(block=True, action="HARD_BLOCK")
    with patch("app.db.redis_client.get_redis", return_value=redis), \
         patch("app.worker.tasks.run_agent_pipeline", fake_task), \
         patch.dict(sys.modules, {"app.services.llm_cost_budget": blocked_stub}):
        summary = await flush_pending(now=1000.0)

    assert summary["fired"] == 0
    assert summary["blocked"] == 2
    fake_task.apply_async.assert_not_called()
    # ZREM still cleans up the blocked members.
    redis.zrem.assert_awaited_once()
    # Degraded marker written so /health/ingestion can surface it.
    redis.set.assert_awaited()


@pytest.mark.asyncio
async def test_flush_pending_threads_mode_override_into_task(enable_debouncer):
    """``mode_override="rules"`` from the budget gate must reach the
    pipeline task — that's how the analysis-router knows to skip the
    LLM and use rules+ML instead."""
    from app.services.ai_pipeline_debouncer import flush_pending

    members = ["proj-1|run-A|42|offline"]
    redis = _fake_redis()
    redis.zrangebyscore = AsyncMock(return_value=members)
    fake_task = MagicMock()
    fake_task.apply_async = MagicMock()

    soft_stub = _stub_llm_cost_budget_module(mode_override="rules", action="DOWNGRADE_RULES")
    with patch("app.db.redis_client.get_redis", return_value=redis), \
         patch("app.worker.tasks.run_agent_pipeline", fake_task), \
         patch.dict(sys.modules, {"app.services.llm_cost_budget": soft_stub}):
        summary = await flush_pending(now=1000.0)

    assert summary["fired"] == 1
    assert summary["fallback"] == 1
    # The mode_override travels as a Celery header so the task body
    # can read it without changing its kwargs signature.
    _args, kwargs = fake_task.apply_async.call_args
    assert kwargs["headers"] == {"ai_mode_override": "rules"}


@pytest.mark.asyncio
async def test_flush_pending_respects_window(enable_debouncer):
    """A run scored at ``now - 30`` with a 60s debounce window is NOT
    eligible yet — flush_pending must pass the right ZRANGEBYSCORE
    upper bound (``now - window``)."""
    from app.services.ai_pipeline_debouncer import flush_pending

    redis = _fake_redis()
    redis.zrangebyscore = AsyncMock(return_value=[])
    with patch("app.db.redis_client.get_redis", return_value=redis):
        await flush_pending(now=1000.0, window_seconds=60)

    # The eligible-until cutoff is now - window = 940. Pin that the
    # debouncer passes the right bound to Redis.
    args, kwargs = redis.zrangebyscore.call_args
    # signature: zrangebyscore(key, min, max, start=..., num=...)
    assert args[0] == "testlookup:ai_pipeline_debounce"
    assert args[1] == 0
    assert args[2] == 940.0


@pytest.mark.asyncio
async def test_flush_pending_groups_per_project(enable_debouncer):
    """Multiple projects each get an independent budget check + fan-out.
    Two projects, one drained per fan-out cycle, each fires its own runs."""
    from app.services.ai_pipeline_debouncer import flush_pending

    members = [
        "proj-A|run-1|1|offline",
        "proj-B|run-2|2|offline",
        "proj-A|run-3|3|offline",
    ]
    redis = _fake_redis()
    redis.zrangebyscore = AsyncMock(return_value=members)
    fake_task = MagicMock()
    fake_task.apply_async = MagicMock()

    stub = _stub_llm_cost_budget_module()
    with patch("app.db.redis_client.get_redis", return_value=redis), \
         patch("app.worker.tasks.run_agent_pipeline", fake_task), \
         patch.dict(sys.modules, {"app.services.llm_cost_budget": stub}):
        summary = await flush_pending(now=1000.0)

    assert summary["projects"] == 2
    assert summary["fired"] == 3
    assert "proj-A" in summary["per_project"]
    assert summary["per_project"]["proj-A"]["fired"] == 2
    assert summary["per_project"]["proj-B"]["fired"] == 1
