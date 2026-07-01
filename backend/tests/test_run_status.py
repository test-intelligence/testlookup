"""Regression: a finalized run that executed nothing must not grade as PASSED.

Bug (2026-07, B5): ``_update_run_aggregates`` (file path) and
``upsert_test_run`` (live close path) set ``status = FAILED if failed+broken>0
else PASSED``. A run that ingested 0 test cases (empty/parse-failed upload) or a
fully-skipped suite therefore became PASSED with pass_rate 0.0 and flowed into
trends and the release gate as a *green* run. Both paths now share
``terminal_run_status``, which returns STOPPED when nothing executed.
"""
from __future__ import annotations

from app.models.postgres import LaunchStatus
from app.services.run_status import terminal_run_status


def test_empty_run_is_stopped_not_passed():
    """0 executed (empty/parse-failed upload) → STOPPED, never PASSED."""
    assert terminal_run_status(executed=0, failed=0, broken=0) == LaunchStatus.STOPPED


def test_all_skipped_run_is_stopped():
    """A run with tests but nothing executed (all skipped) also STOPPED —
    pass_rate would be 0.0, so PASSED would be a contradiction."""
    # executed already excludes skipped, so an all-skipped run arrives as 0.
    assert terminal_run_status(executed=0, failed=0, broken=0) == LaunchStatus.STOPPED


def test_run_with_failures_is_failed():
    assert terminal_run_status(executed=10, failed=2, broken=0) == LaunchStatus.FAILED


def test_run_with_broken_is_failed():
    """BROKEN counts as a failure for the terminal verdict."""
    assert terminal_run_status(executed=10, failed=0, broken=1) == LaunchStatus.FAILED


def test_all_passing_run_is_passed():
    assert terminal_run_status(executed=10, failed=0, broken=0) == LaunchStatus.PASSED


def test_negative_guard_is_stopped():
    """Defensive: a non-positive executed count never grades PASSED."""
    assert terminal_run_status(executed=-1, failed=0, broken=0) == LaunchStatus.STOPPED
