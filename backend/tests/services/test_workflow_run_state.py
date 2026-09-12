"""E7.1: the pipeline run state machine is the only legal writer of status.

These tests pin the transition table, the legacy mappings (``partial`` and
``cancelled`` may be READ, never written), the public projection, and the
resume rule that replaced ``status == "partial"``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.enums import PipelineRunStatus as S
from app.services import workflow_run_state as wrs


def _run(status="running", **kw):
    base = dict(status=status, completed_at=None, error=None, execution_metadata=None)
    base.update(kw)
    return SimpleNamespace(**base)


# ── transition table ─────────────────────────────────────────────────────────


ALLOWED = {
    (S.PENDING, S.RUNNING), (S.PENDING, S.FAILED),
    (S.RUNNING, S.COMPLETED), (S.RUNNING, S.FAILED), (S.RUNNING, S.RETRY_WAIT), (S.RUNNING, S.PENDING),
    (S.RETRY_WAIT, S.RUNNING), (S.RETRY_WAIT, S.FAILED), (S.RETRY_WAIT, S.PENDING),
    (S.COMPLETED, S.PASSED), (S.COMPLETED, S.FAILED), (S.COMPLETED, S.RUNNING),
    (S.FAILED, S.RUNNING), (S.FAILED, S.PENDING), (S.FAILED, S.RETRY_WAIT),
}


@pytest.mark.parametrize("src", list(S))
@pytest.mark.parametrize("dst", list(S))
def test_every_pair_is_either_listed_or_rejected(src, dst):
    run = _run(src.value)
    if src is dst:
        wrs.apply_transition(run, dst)  # same-state no-op is always fine
        assert run.status == dst.value
    elif (src, dst) in ALLOWED:
        wrs.apply_transition(run, dst)
        assert run.status == dst.value
    else:
        with pytest.raises(wrs.IllegalTransition):
            wrs.apply_transition(run, dst)
        assert run.status == src.value, "a rejected transition must not touch the row"


def test_passed_is_terminal():
    assert wrs.TRANSITIONS[S.PASSED] == frozenset()


def test_table_matches_the_pinned_edges():
    edges = {(a, b) for a, targets in wrs.TRANSITIONS.items() for b in targets}
    assert edges == ALLOWED


# ── side effects of a transition ─────────────────────────────────────────────


def test_terminal_transition_stamps_completed_at_once():
    stamp = datetime(2026, 9, 11, tzinfo=timezone.utc)
    run = _run("running")
    wrs.apply_transition(run, S.COMPLETED, now=stamp)
    assert run.completed_at == stamp
    later = datetime(2026, 9, 12, tzinfo=timezone.utc)
    wrs.apply_transition(run, S.PASSED, now=later)
    assert run.completed_at == stamp, "an existing completed_at is never overwritten"


def test_running_clears_completed_at_and_error():
    run = _run("failed", completed_at=datetime.now(timezone.utc), error="boom")
    wrs.apply_transition(run, S.RUNNING)
    assert run.completed_at is None
    assert run.error is None


def test_failed_without_error_gets_a_default_error():
    run = _run("running")
    wrs.apply_transition(run, S.FAILED)
    assert run.error, "status='failed' => error IS NOT NULL (section 7.2 invariant)"


def test_error_is_truncated_to_2000():
    run = _run("running")
    wrs.apply_transition(run, S.FAILED, error="x" * 5000)
    assert len(run.error) == 2000


def test_degraded_completion_stamps_stage_quality():
    run = _run("running", execution_metadata={"tools_used": ["a"]})
    wrs.apply_transition(run, S.COMPLETED, degraded=True)
    assert run.status == "completed"
    assert run.execution_metadata == {"tools_used": ["a"], "stage_quality": "degraded"}


# ── legacy vocabulary: readable, never writable ──────────────────────────────


def test_cancelled_maps_to_failed_with_prefix():
    run = _run("running")
    wrs.apply_transition(run, "cancelled", error="user stopped it")
    assert run.status == "failed"
    assert run.error == "cancelled: user stopped it"


def test_cancelled_without_error_still_says_cancelled():
    run = _run("running")
    wrs.apply_transition(run, "cancelled")
    assert run.error.startswith(wrs.CANCELLED_ERROR_PREFIX)


def test_partial_requested_as_target_becomes_completed():
    run = _run("running")
    wrs.apply_transition(run, "partial")
    assert run.status == "completed"


def test_partial_row_normalises_to_completed_and_is_resumable_only_when_degraded():
    assert wrs.normalize_status("partial") is S.COMPLETED
    assert wrs.is_resumable("partial", {"stage_quality": "degraded"}) is True
    assert wrs.is_resumable("completed", {"stage_quality": "degraded"}) is True
    assert wrs.is_resumable("completed", {}) is False
    assert wrs.is_resumable("failed", None) is True
    assert wrs.is_resumable("passed", {"stage_quality": "degraded"}) is False
    assert wrs.is_resumable("running", None) is False


def test_unknown_status_is_treated_as_failed_not_in_progress():
    # An unrecognised status is an invalid state; projecting it as in-progress
    # would recreate the stuck-forever failure mode.
    assert wrs.normalize_status("garbage") is S.FAILED
    assert wrs.public_status(None) == "failed"


# ── public projection ────────────────────────────────────────────────────────


@pytest.mark.parametrize("internal,public", [
    ("pending", "in_progress"), ("running", "in_progress"), ("retry_wait", "in_progress"),
    ("completed", "completed"), ("passed", "passed"), ("failed", "failed"),
    ("partial", "completed"), ("cancelled", "failed"),
])
def test_public_status_is_one_of_four(internal, public):
    assert wrs.public_status(internal) == public
    assert public in {"in_progress", "completed", "failed", "passed"}


def test_is_terminal():
    assert all(wrs.is_terminal(s) for s in ("completed", "passed", "failed", "partial", "cancelled"))
    assert not any(wrs.is_terminal(s) for s in ("pending", "running", "retry_wait"))


# ── guarded_transition builds the race-safe statement ────────────────────────


@pytest.mark.asyncio
async def test_guarded_transition_raises_when_zero_rows():
    class _Result:
        def first(self):
            return None

    class _DB:
        async def execute(self, stmt):
            self.stmt = stmt
            return _Result()

    db = _DB()
    with pytest.raises(wrs.TransitionLost):
        await wrs.guarded_transition(
            db, "00000000-0000-0000-0000-000000000001", expected="running", to="failed", error="x"
        )
    compiled = str(db.stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "status = 'running'" in compiled
    assert "RETURNING" in compiled.upper()


@pytest.mark.asyncio
async def test_guarded_transition_rejects_illegal_edge_before_touching_db():
    class _DB:
        async def execute(self, stmt):  # pragma: no cover - must not be reached
            raise AssertionError("illegal edge reached the database")

    with pytest.raises(wrs.IllegalTransition):
        await wrs.guarded_transition(_DB(), "00000000-0000-0000-0000-000000000001", expected="passed", to="running")


@pytest.mark.asyncio
async def test_guarded_transition_adds_fencing_predicate():
    class _Result:
        def first(self):
            return ("failed",)

    class _DB:
        async def execute(self, stmt):
            self.stmt = stmt
            return _Result()

    db = _DB()
    out = await wrs.guarded_transition(
        db, "00000000-0000-0000-0000-000000000001", expected="running", to="failed",
        fencing_token="tok-1", error="lease",
    )
    assert out is S.FAILED
    compiled = str(db.stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "fencing_token = 'tok-1'" in compiled
