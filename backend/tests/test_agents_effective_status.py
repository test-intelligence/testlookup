"""Unit tests for the /api/v1/agents effective-status derivation.

User-reported bug: a pipeline whose ingestion stage failed was rendered
as RUNNING in the /agents panel because ``AgentPipelineRun.status``
stayed at ``"running"`` after a worker crash that bypassed
``_mark_pipeline_done``. The read-side derivation in
``routers/agents._apply_effective_status`` is the user-facing safety net
for that gap.

What's pinned here:

* A pipeline with status="running" but at least one failed stage is
  reported as "failed".
* A pipeline that has been running past the stale threshold without a
  ``completed_at`` is reported as "failed" even when no stage is yet
  marked failed (covers SIGKILL between stage transitions).
* A genuinely-running pipeline (started recently, no failed stages) is
  untouched.
* Completed / partial / already-failed pipelines are never mutated by
  the derivation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from app.routers.agents import (  # noqa: E402
    RUNNING_STALE_THRESHOLD,
    _apply_effective_status,
    _is_stale_running,
)


def _pipeline(
    *,
    status: str = "running",
    started_offset: timedelta | None = None,
    completed_at: datetime | None = None,
) -> SimpleNamespace:
    """Build a fake pipeline row with the columns the derivation reads."""
    now = datetime.now(timezone.utc)
    started_at = (now - started_offset) if started_offset else now
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        started_at=started_at,
        completed_at=completed_at,
    )


# ── _is_stale_running ────────────────────────────────────────────────────────


class TestIsStaleRunning:
    def test_not_running_pipeline_is_never_stale(self):
        p = _pipeline(status="completed", started_offset=timedelta(hours=24))
        assert _is_stale_running(p, now=datetime.now(timezone.utc)) is False

    def test_running_pipeline_with_completed_at_is_not_stale(self):
        p = _pipeline(
            status="running",
            started_offset=timedelta(hours=1),
            completed_at=datetime.now(timezone.utc),
        )
        assert _is_stale_running(p, now=datetime.now(timezone.utc)) is False

    def test_running_pipeline_within_threshold_is_not_stale(self):
        p = _pipeline(status="running", started_offset=timedelta(minutes=5))
        assert _is_stale_running(p, now=datetime.now(timezone.utc)) is False

    def test_running_pipeline_past_threshold_is_stale(self):
        p = _pipeline(
            status="running",
            started_offset=RUNNING_STALE_THRESHOLD + timedelta(minutes=5),
        )
        assert _is_stale_running(p, now=datetime.now(timezone.utc)) is True

    def test_running_pipeline_without_started_at_is_not_stale(self):
        # Defensive: if started_at is missing, we can't decide based on age
        # and we don't override the status.
        p = _pipeline(status="running")
        p.started_at = None
        assert _is_stale_running(p, now=datetime.now(timezone.utc)) is False


# ── _apply_effective_status ──────────────────────────────────────────────────


class TestApplyEffectiveStatus:
    def test_running_with_failed_stage_becomes_failed(self):
        p = _pipeline(status="running", started_offset=timedelta(minutes=2))
        _apply_effective_status(
            [p], failed_stage_ids={p.id}, now=datetime.now(timezone.utc),
        )
        assert p.status == "failed"

    def test_running_past_threshold_becomes_failed(self):
        p = _pipeline(
            status="running",
            started_offset=RUNNING_STALE_THRESHOLD + timedelta(minutes=10),
        )
        _apply_effective_status(
            [p], failed_stage_ids=set(), now=datetime.now(timezone.utc),
        )
        assert p.status == "failed"

    def test_running_fresh_with_no_failures_is_untouched(self):
        p = _pipeline(status="running", started_offset=timedelta(minutes=1))
        _apply_effective_status(
            [p], failed_stage_ids=set(), now=datetime.now(timezone.utc),
        )
        assert p.status == "running"

    def test_completed_pipeline_untouched_even_if_stage_failed(self):
        # ``partial`` and ``completed`` are produced by ``_mark_pipeline_done``
        # which already accounts for stage failures — don't second-guess it.
        p = _pipeline(
            status="completed",
            started_offset=timedelta(hours=24),
            completed_at=datetime.now(timezone.utc),
        )
        _apply_effective_status(
            [p], failed_stage_ids={p.id}, now=datetime.now(timezone.utc),
        )
        assert p.status == "completed"

    def test_partial_pipeline_untouched(self):
        p = _pipeline(
            status="partial",
            started_offset=timedelta(hours=24),
            completed_at=datetime.now(timezone.utc),
        )
        _apply_effective_status(
            [p], failed_stage_ids={p.id}, now=datetime.now(timezone.utc),
        )
        assert p.status == "partial"

    def test_already_failed_pipeline_untouched(self):
        p = _pipeline(
            status="failed",
            started_offset=timedelta(hours=2),
            completed_at=datetime.now(timezone.utc),
        )
        _apply_effective_status(
            [p], failed_stage_ids=set(), now=datetime.now(timezone.utc),
        )
        assert p.status == "failed"

    def test_mixed_list_only_mutates_affected_rows(self):
        p_running_failed_stage = _pipeline(status="running", started_offset=timedelta(minutes=2))
        p_running_fresh = _pipeline(status="running", started_offset=timedelta(minutes=2))
        p_completed = _pipeline(
            status="completed", completed_at=datetime.now(timezone.utc),
        )

        _apply_effective_status(
            [p_running_failed_stage, p_running_fresh, p_completed],
            failed_stage_ids={p_running_failed_stage.id},
            now=datetime.now(timezone.utc),
        )

        assert p_running_failed_stage.status == "failed"
        assert p_running_fresh.status == "running"
        assert p_completed.status == "completed"
