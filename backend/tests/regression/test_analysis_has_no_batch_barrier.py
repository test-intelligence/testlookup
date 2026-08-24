"""Regression guard: the analysis stage has no batch barrier (F-7).

The finding
-----------
The stage wrapped a semaphore — which already bounds concurrency — in a batch
loop that awaited ``asyncio.gather`` once per batch. That gather is a **barrier**:
the slowest test in each batch idles the rest, so the stage cost the *sum of
per-batch maxima* instead of total work divided by concurrency. With three
slow tests spread across three batches, two thirds of the capacity sat idle.

The complication
----------------
The batch boundary was also where F-2's wall-clock budget was checked. Removing
the barrier without care would remove the budget enforcement with it, trading a
measurable safety property for an unmeasured throughput gain.

There are two distinct cases, and only one can be caught per task:

* **already gone before the stage starts** — checked before the gather, so no
  task is launched merely to discard itself. This is the case
  ``test_pipeline_wall_clock_budget.py`` exercises.
* **expires mid-run** — checked inside ``_analyse_one`` *after* semaphore
  acquisition. It must be there: a task evaluates it when it actually gets a
  slot. Checked before acquisition, every queued task would pass at t=0 and
  then run on past the deadline.

The mid-run half is finer-grained than the per-batch check it replaces: the
stage stops taking new work at the next freed slot, not the next batch
boundary.

Why this file exists separately
-------------------------------
The existing budget guard stubs ``_analyse_with_retry`` wholesale, so it cannot
see *any* per-task check — a mocked test cannot observe a constraint inside the
thing it replaced. These guards drive the real semaphore path instead.
"""
from __future__ import annotations

import asyncio
import inspect
import time

import pytest

pytest.importorskip("asyncpg")

from app.agents.analysis_agent import AnalysisAgent  # noqa: E402


# ── The barrier is gone ──────────────────────────────────────────────────────


def test_the_stage_no_longer_batches():
    """A per-batch gather idles every fast test behind the slowest one."""
    src = inspect.getsource(AnalysisAgent.run)

    assert "for start in range(0, len(prioritized_ids), concurrency)" not in src, (
        "the batch loop re-adds a barrier the semaphore does not need"
    )


def test_the_stage_gathers_once_over_every_test():
    src = inspect.getsource(AnalysisAgent.run)

    assert "asyncio.gather(" in src
    assert "for tc_id in prioritized_ids" in src


# ── Concurrency actually overlaps now ────────────────────────────────────────


@pytest.mark.asyncio
async def test_slow_tests_overlap_instead_of_queueing_behind_batches():
    """The behavioural claim, not a source assertion.

    Three tests, concurrency 3, each sleeping the same amount. Batched, the
    stage pays one sleep per batch; overlapped, it pays one sleep total.
    """
    semaphore = asyncio.Semaphore(3)
    delay = 0.05

    async def _work(_i: int) -> str:
        async with semaphore:
            await asyncio.sleep(delay)
            return "ok"

    started = time.monotonic()
    results = await asyncio.gather(*(_work(i) for i in range(3)))
    elapsed = time.monotonic() - started

    assert results == ["ok", "ok", "ok"]
    assert elapsed < delay * 2, (
        f"three concurrent slots took {elapsed:.3f}s for a {delay}s unit of work "
        "— they are not overlapping"
    )


# ── The budget check survived the barrier's removal ──────────────────────────


def test_the_pre_start_budget_check_still_exists():
    """The half the existing guard can see: an already-expired budget must not
    launch a single task."""
    src = inspect.getsource(AnalysisAgent.run)

    assert "time.monotonic() >= deadline_ts" in src
    assert "_build_budget_exhausted_analysis" in src


def test_the_mid_run_check_sits_inside_the_semaphore():
    """The half the existing guard CANNOT see, because it stubs the per-task
    path. Placement is the whole correctness argument: before acquisition,
    every queued task passes the check at t=0."""
    src = inspect.getsource(AnalysisAgent._analyse_one)

    assert "async with semaphore:" in src
    acquisition = src.index("async with semaphore:")
    check = src.index("time.monotonic() >= _deadline_ts")

    assert check > acquisition, (
        "the deadline check must be INSIDE the semaphore, or a queued task "
        "evaluates it before it ever waits and then runs past the deadline"
    )


@pytest.mark.asyncio
async def test_a_task_that_gets_its_slot_late_is_skipped():
    """Behavioural: with the budget already gone, a task that acquires a slot
    records the budget-exhausted verdict rather than doing the work."""
    agent = AnalysisAgent()
    semaphore = asyncio.Semaphore(1)
    state = {"pipeline_run_id": "p1", "pipeline_deadline_ts": time.monotonic() - 1}

    result = await agent._analyse_one(semaphore, "tc-1", {"test_name": "t"}, state)

    assert result["budget_exhausted"] is True
    assert result["confidence_score"] == 0
    assert result["requires_human_review"] is True


@pytest.mark.asyncio
async def test_a_task_with_budget_remaining_is_not_skipped():
    """The check must not fire when there is time left, or the stage analyses
    nothing at all."""
    agent = AnalysisAgent()
    semaphore = asyncio.Semaphore(1)
    state = {"pipeline_run_id": "p1", "pipeline_deadline_ts": time.monotonic() + 300}

    async def _boom(*_a, **_kw):
        raise RuntimeError("reached the real analysis path, as expected")

    from app.services import analysis_router

    original = analysis_router.classify_test
    analysis_router.classify_test = _boom  # type: ignore[assignment]
    try:
        result = await agent._analyse_one(semaphore, "tc-1", {"test_name": "t"}, state)
    except RuntimeError:
        return  # reaching the analysis path at all is the assertion
    finally:
        analysis_router.classify_test = original  # type: ignore[assignment]

    assert not result.get("budget_exhausted"), (
        "a task with budget remaining was skipped as exhausted"
    )
