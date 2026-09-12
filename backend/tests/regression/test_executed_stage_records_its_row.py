"""Regression guard: a node that ran is recorded as having run.

The defect
----------
``AgentStageResult`` rows are seeded ``pending`` and flipped to ``completed`` by
``BaseAgent.mark_stage_done``. But ``BaseAgent.run`` is abstract, so that
lifecycle is **opt-in per subclass** -- fifteen agents call it and three do not:

* ``change_ownership_agent`` -- a ``BaseAgent``, correct ``stage_name``, but it
  never calls ``mark_stage_running`` / ``mark_stage_done``;
* ``contract_agent`` and ``log_intelligence_agent`` -- not ``BaseAgent``
  subclasses at all, so they have no lifecycle to call.

The two ``cluster_investigation_*`` nodes are plain functions and were never
covered either. Their five rows therefore sat at ``pending`` to the end of the
run, where the sweep in ``_persist_deep_outputs`` relabels anything still
pending as ``skipped`` / "Stage not on active pipeline branch".

Measured on the deployment 2026-08-24: the pipeline's own ``completed_stages``
listed all five as run, in all six runs, while ``agent_stage_results`` called
them skipped. Two records of one fact, disagreeing -- the same shape as the
plan-rebuild defect fixed just before this, and the reason F-9's fan-out could
not be measured: the rows that would carry ``started_at`` / ``completed_at`` for
the fanned-out specialists were never written, so there was no overlap to read.

``_checkpoint_stage`` only writes when the row already reads ``completed``, so
those stages were silently discarding their checkpoint data too.

What is guarded
---------------
* a node that runs gets a ``completed`` row with both timestamps, even when its
  agent writes nothing;
* the backfill runs BEFORE the checkpoint, so checkpoint data lands;
* a row an agent already completed is not touched -- the wrapper fills a gap, it
  does not become a second writer of tokens/cost/confidence it cannot know;
* ``failed`` and self-reported ``skipped`` rows are preserved;
* a node that returns a skip delta is not recorded as completed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")

from app.agents import workflow as wf  # noqa: E402


class _StageRow:
    """Stand-in for the AgentStageResult row the helper mutates."""

    def __init__(self, status, started_at=None):
        self.status = status
        self.started_at = started_at
        self.completed_at = None


@pytest.fixture
def quiet_workflow(monkeypatch):
    monkeypatch.setattr(wf, "emit_event", AsyncMock())
    monkeypatch.setattr(wf, "_checkpoint_stage", AsyncMock())
    monkeypatch.setattr(wf, "_write_stage_skipped", AsyncMock())
    monkeypatch.setattr(wf, "_planner_stage_selection", lambda _s, _n: (True, "selected"))


@pytest.fixture
def captured(monkeypatch):
    """Capture backfill calls made by the wrapper."""
    calls: list[dict] = []

    async def _record(pipeline_run_id, stage_name, *, started_at, fencing_token=None):
        calls.append({
            "pipeline_run_id": pipeline_run_id,
            "stage_name": stage_name,
            "started_at": started_at,
        })

    monkeypatch.setattr(wf, "_mark_stage_executed", _record)
    return calls


# ── The stage that ran is recorded ───────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [
    "cluster_investigation_dispatch",
    "cluster_investigation_join",
    "contract_validation",
    "log_intelligence",
    "change_ownership",
])
async def test_a_node_that_ran_is_marked_executed(quiet_workflow, captured, stage):
    """The five stages whose agents write no row of their own."""
    async def _node(_state):
        return {"completed_stages": [stage]}

    wrapped = wf._make_checkpointed_node(_node, stage)
    await wrapped({"pipeline_run_id": "pipe-1"})

    assert [c["stage_name"] for c in captured] == [stage]
    assert isinstance(captured[0]["started_at"], datetime)


@pytest.mark.asyncio
async def test_the_backfill_precedes_the_checkpoint(monkeypatch, quiet_workflow):
    """_checkpoint_stage only writes a row that already reads completed."""
    order: list[str] = []

    async def _mark(pipeline_run_id, stage_name, *, started_at, fencing_token=None):
        order.append("mark")

    async def _checkpoint(*_a, **_k):
        order.append("checkpoint")

    monkeypatch.setattr(wf, "_mark_stage_executed", _mark)
    monkeypatch.setattr(wf, "_checkpoint_stage", _checkpoint)

    async def _node(_state):
        return {"completed_stages": ["contract_validation"]}

    wrapped = wf._make_checkpointed_node(_node, "contract_validation")
    await wrapped({"pipeline_run_id": "pipe-1"})

    assert order == ["mark", "checkpoint"], (
        "checkpoint data is dropped when the row is not yet completed"
    )


@pytest.mark.asyncio
async def test_a_self_reported_skip_is_not_recorded_as_completed(quiet_workflow, captured):
    """A flag-gated node early-returns a skip delta; that is not execution."""
    async def _node(_state):
        return {
            "completed_stages": ["change_ownership"],
            "skipped_stages": ["change_ownership"],
        }

    wrapped = wf._make_checkpointed_node(_node, "change_ownership")
    await wrapped({"pipeline_run_id": "pipe-1"})

    assert captured == []


# ── The backfill fills a gap; it does not overwrite ──────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "running"])
async def test_an_unwritten_row_is_completed_with_timestamps(monkeypatch, status):
    row = _StageRow(status)
    _install_fake_session(monkeypatch, row)
    started = datetime.now(timezone.utc) - timedelta(seconds=3)

    await wf._mark_stage_executed("pipe-1", "log_intelligence", started_at=started)

    assert row.status == "completed"
    assert row.started_at == started
    assert row.completed_at is not None
    assert row.completed_at > row.started_at


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "skipped"])
async def test_a_row_another_writer_owns_is_left_alone(monkeypatch, status):
    """An agent's completed row carries tokens/cost the wrapper cannot rebuild."""
    own_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = _StageRow(status, started_at=own_start)
    _install_fake_session(monkeypatch, row)

    await wf._mark_stage_executed("pipe-1", "regression_watchman",
                                  started_at=datetime.now(timezone.utc))

    assert row.status == status
    assert row.started_at == own_start
    assert row.completed_at is None


@pytest.mark.asyncio
async def test_an_agent_start_time_is_preserved(monkeypatch):
    """mark_stage_running already set a truer start than the wrapper's."""
    own_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = _StageRow("running", started_at=own_start)
    _install_fake_session(monkeypatch, row)

    await wf._mark_stage_executed("pipe-1", "change_ownership",
                                  started_at=datetime.now(timezone.utc))

    assert row.status == "completed"
    assert row.started_at == own_start


def _install_fake_session(monkeypatch, row):
    """Point the helper's AsyncSessionLocal at a session yielding `row`."""
    class _Result:
        def scalar_one_or_none(self):
            return row

    class _Session:
        async def execute(self, _stmt):
            return _Result()

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(wf, "AsyncSessionLocal", lambda: _Session())
