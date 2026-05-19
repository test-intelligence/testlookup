"""Regression: ``/api/v1/runs/compare`` raised LookupError when one
or both runs had no ``test_cases`` rows for the requested suite,
even when both runs were tagged with that suite at the run level.

Bug pinned (2026-05-19): user reported "Suite RealisticTestNGSuite
was not found in the left and right run" on the compare page even
though 47 runs were available via the resolver. Root cause: passing
live-stream runs that finalised before placeholder synthesis kicked
in (or runs whose per-test buffer evicted before the drainer fired)
have a non-NULL ``primary_suite_name`` but zero rows in
``test_cases`` for that suite — so ``_load_test_rows`` returned an
empty dict and ``compare_runs`` raised LookupError → frontend
rendered "Not enough data to compare".

Fix: when both runs carry the suite via ``primary_suite_name`` (the
resolver already proved this) but per-test rows are missing, fall
through with an empty delta list and a ``data_gap=True`` flag in
the response. The UI now renders an aggregate-only diff with a
"per-test detail unavailable" banner instead of a generic error.

What this file pins:

  * ``compare_runs`` no longer raises when both runs are tagged but
    have no per-test rows.
  * The returned payload carries ``data_gap=True`` so the UI can
    render the aggregate-only mode.
  * It STILL raises when neither run is tagged AND has no rows
    (genuine "wrong suite" case — the original error message is
    still correct).
  * Normal happy path (both runs have rows) returns ``data_gap=False``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _run(**overrides):
    base = dict(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        build_number="build-x",
        branch="main",
        commit_hash=None,
        status="COMPLETED",
        total_tests=10,
        passed_tests=10,
        failed_tests=0,
        broken_tests=0,
        skipped_tests=0,
        pass_rate=100.0,
        duration_ms=12345,
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
        primary_suite_name="RealisticTestNGSuite",
        suite_names=["RealisticTestNGSuite"],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_compare_returns_data_gap_when_both_runs_tagged_but_no_rows():
    """The user's reported case: 47 runs available, both tagged with the
    suite at run level, no per-test rows — must not raise."""
    from app.services.run_compare_service import compare_runs

    left = _run()
    right = _run()
    db = AsyncMock()

    with patch("app.services.run_compare_service._load_summary",
               AsyncMock(side_effect=[left, right])), \
         patch("app.services.run_compare_service._load_test_rows",
               AsyncMock(side_effect=[{}, {}])):
        result = await compare_runs(
            db, left.id, right.id, suite_name="RealisticTestNGSuite",
        )
    assert result["data_gap"] is True
    assert result["test_deltas"] == []
    # Aggregate diff still computable from the run-level summaries.
    assert result["scope"] == "suite"
    assert result["suite_name"] == "RealisticTestNGSuite"


@pytest.mark.asyncio
async def test_compare_still_raises_when_neither_run_is_tagged():
    """If neither run carries the suite via ``primary_suite_name`` AND
    neither has per-test rows for it, the original error is correct —
    the user picked a suite that doesn't exist on these runs."""
    from app.services.run_compare_service import compare_runs

    left = _run(primary_suite_name="OtherSuite")
    right = _run(primary_suite_name="OtherSuite")
    db = AsyncMock()

    with patch("app.services.run_compare_service._load_summary",
               AsyncMock(side_effect=[left, right])), \
         patch("app.services.run_compare_service._load_test_rows",
               AsyncMock(side_effect=[{}, {}])):
        with pytest.raises(LookupError) as exc:
            await compare_runs(
                db, left.id, right.id, suite_name="RealisticTestNGSuite",
            )
    assert "RealisticTestNGSuite was not found" in str(exc.value)


@pytest.mark.asyncio
async def test_compare_happy_path_sets_data_gap_false():
    """Normal case: both runs have per-test rows. ``data_gap`` is False."""
    from app.services.run_compare_service import compare_runs

    left = _run()
    right = _run()
    left_row = SimpleNamespace(
        test_fingerprint="fp1",
        test_name="t1",
        suite_name="RealisticTestNGSuite",
        status="PASSED",
        duration_ms=100,
    )
    right_row = SimpleNamespace(
        test_fingerprint="fp1",
        test_name="t1",
        suite_name="RealisticTestNGSuite",
        status="PASSED",
        duration_ms=110,
    )
    db = AsyncMock()
    with patch("app.services.run_compare_service._load_summary",
               AsyncMock(side_effect=[left, right])), \
         patch("app.services.run_compare_service._load_test_rows",
               AsyncMock(side_effect=[{"fp1": left_row}, {"fp1": right_row}])):
        result = await compare_runs(
            db, left.id, right.id, suite_name="RealisticTestNGSuite",
        )
    assert result["data_gap"] is False
