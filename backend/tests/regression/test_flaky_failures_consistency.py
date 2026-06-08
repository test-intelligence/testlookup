"""Regression: ``/failures`` and ``/flaky-coach`` must agree on what's flaky.

Bug pinned (2026-06-08): ``/failures`` rendered

    "Not a flake — flake detector found zero intermittents.
     Treat as a hard regression, not a re-run candidate."

while ``/flaky-coach`` listed flaky tests for the *same* project + window.

Root cause: two endpoints, two different flaky definitions.
  * ``/flaky-coach`` (``test_health_coach_service``) merges human-triaged
    ``FLAKY_TEST`` rows (from /my-failures) on top of the auto-detector.
  * ``/failures`` (``analytics_service.flaky_tests``) was auto-detect **only**
    (intermittent ``test_case_history``, >=3 runs, both pass+fail) and ignored
    manual triage — so ``flakyCount`` came back 0 and the page declared the
    failures a hard regression, contradicting /flaky-coach.

Fix: ``analytics_service.flaky_tests`` now merges the same manually-triaged
``FLAKY_TEST`` fingerprints (deduped; auto wins; tagged ``source``), so the two
pages draw flaky signal from one definition.

These tests pin the merge/dedup/limit/tagging logic by mocking ``db.execute``
(the raw SQL itself is exercised by the integration suite).
"""
from __future__ import annotations

import os
import types

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/test")

import pytest

pytest.importorskip("sqlalchemy")

from unittest.mock import AsyncMock  # noqa: E402

from app.services import analytics_service  # noqa: E402


def _row(**cols):
    """A stand-in for a SQLAlchemy Row: ``dict(row._mapping)`` copies cols."""
    return types.SimpleNamespace(_mapping=dict(cols))


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


def _db(auto_rows, manual_rows):
    """A fake AsyncSession whose two ``execute`` calls return auto then manual."""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_Result(auto_rows), _Result(manual_rows)])
    return db


@pytest.mark.asyncio
async def test_manual_flaky_triage_is_merged_into_failures_flaky_list():
    """A manually-triaged FLAKY_TEST with no auto-detected counterpart shows up
    — so /failures stops saying "zero intermittents / hard regression"."""
    auto = [_row(test_fingerprint="A", test_name="a", suite_name="s",
                 class_name="C", project_name="p", total_runs=10, fail_count=3,
                 pass_count=7, failure_rate_pct=30.0, last_seen=None)]
    manual = [_row(test_fingerprint="B", test_name="b", suite_name="s",
                   class_name="C", project_name="p", total_runs=2, fail_count=2,
                   pass_count=0, failure_rate_pct=100.0, last_seen=None)]
    out = await analytics_service.flaky_tests(_db(auto, manual), "proj", 30, 20)

    by = {i["test_fingerprint"]: i for i in out["items"]}
    assert set(by) == {"A", "B"}
    assert by["A"]["source"] == "auto"
    assert by["B"]["source"] == "manual"          # human-flagged surfaced
    assert by["B"]["failure_rate_pct"] == 100.0
    assert out["total"] == 2


@pytest.mark.asyncio
async def test_auto_detection_wins_dedup_over_manual_for_same_fingerprint():
    """A test that is BOTH auto-detected and manually triaged appears once,
    keeping the auto row (it carries the real measured ratio)."""
    auto = [_row(test_fingerprint="A", test_name="a", suite_name="s",
                 class_name="C", project_name="p", total_runs=10, fail_count=4,
                 pass_count=6, failure_rate_pct=40.0, last_seen=None)]
    manual = [_row(test_fingerprint="A", test_name="a", suite_name="s",
                   class_name="C", project_name="p", total_runs=4, fail_count=4,
                   pass_count=0, failure_rate_pct=100.0, last_seen=None)]
    out = await analytics_service.flaky_tests(_db(auto, manual), "proj", 30, 20)

    assert len(out["items"]) == 1
    assert out["items"][0]["source"] == "auto"
    assert out["items"][0]["failure_rate_pct"] == 40.0  # real ratio, not the marker


@pytest.mark.asyncio
async def test_manual_query_skipped_when_auto_already_fills_limit():
    """If the auto detector already returns ``limit`` rows there's no room (and
    no need) for manual rows — the second query must not run."""
    auto = [_row(test_fingerprint="A", test_name="a", suite_name="s",
                 class_name="C", project_name="p", total_runs=10, fail_count=3,
                 pass_count=7, failure_rate_pct=30.0, last_seen=None)]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_Result(auto), _Result([])])
    out = await analytics_service.flaky_tests(db, "proj", 30, 1)  # limit == 1

    assert len(out["items"]) == 1
    assert db.execute.await_count == 1               # manual query never issued


@pytest.mark.asyncio
async def test_no_flakes_at_all_returns_empty_so_hard_regression_copy_is_valid():
    """When neither auto nor manual finds anything, the list is genuinely empty
    — the "treat as a hard regression" message is then correct."""
    out = await analytics_service.flaky_tests(_db([], []), "proj", 30, 20)
    assert out["items"] == []
    assert out["total"] == 0


@pytest.mark.asyncio
async def test_manual_query_is_tenant_scoped_by_allowed_project_ids():
    """The merged manual query must carry the same tenant scope as the auto one —
    when scoped by membership (project_id=None + allowed_project_ids), the manual
    query's bound params must include the allowed-id placeholders (defence in
    depth via ``_tenant_filter``)."""
    import uuid as _uuid
    pid = _uuid.uuid4()
    auto = [_row(test_fingerprint="A", test_name="a", suite_name="s",
                 class_name="C", project_name="p", total_runs=10, fail_count=3,
                 pass_count=7, failure_rate_pct=30.0, last_seen=None)]
    db = _db(auto, [])
    await analytics_service.flaky_tests(
        db, None, 30, 20, allowed_project_ids=[pid],
    )
    assert db.execute.await_count == 2          # auto + manual both ran
    manual_params = db.execute.await_args_list[1].args[1]
    assert manual_params.get("pid_0") == str(pid)   # manual query is scoped too
    assert "flaky_status" in manual_params          # ...and it is the manual query


@pytest.mark.asyncio
async def test_manual_query_passes_through_suite_filter():
    """A suite filter on /failures must bind on the manual query too, so manual
    flakes are scoped to the same suite the user is looking at."""
    auto = [_row(test_fingerprint="A", test_name="a", suite_name="API",
                 class_name="C", project_name="p", total_runs=10, fail_count=3,
                 pass_count=7, failure_rate_pct=30.0, last_seen=None)]
    db = _db(auto, [])
    await analytics_service.flaky_tests(db, "proj", 30, 20, suite_name="API Tests")
    manual_params = db.execute.await_args_list[1].args[1]
    assert manual_params.get("suite_name") == "api tests"   # normalised + bound


@pytest.mark.asyncio
async def test_manual_row_with_null_fingerprint_is_skipped():
    """A manual row with a NULL/empty fingerprint must never enter the merged
    list (it can't be deduped or routed to a test)."""
    auto = []
    manual = [
        _row(test_fingerprint=None, test_name="x", suite_name="s",
             class_name="C", project_name="p", total_runs=1, fail_count=1,
             pass_count=0, failure_rate_pct=100.0, last_seen=None),
        _row(test_fingerprint="B", test_name="b", suite_name="s",
             class_name="C", project_name="p", total_runs=2, fail_count=2,
             pass_count=0, failure_rate_pct=100.0, last_seen=None),
    ]
    out = await analytics_service.flaky_tests(_db(auto, manual), "proj", 30, 20)
    fps = {i["test_fingerprint"] for i in out["items"]}
    assert fps == {"B"}                         # null one dropped


@pytest.mark.asyncio
async def test_merged_results_respect_the_limit():
    """auto + manual combined is capped at ``limit``."""
    auto = [_row(test_fingerprint=f"A{i}", test_name="a", suite_name="s",
                 class_name="C", project_name="p", total_runs=10, fail_count=3,
                 pass_count=7, failure_rate_pct=30.0, last_seen=None)
            for i in range(2)]
    manual = [_row(test_fingerprint=f"M{i}", test_name="m", suite_name="s",
                   class_name="C", project_name="p", total_runs=2, fail_count=2,
                   pass_count=0, failure_rate_pct=100.0, last_seen=None)
              for i in range(5)]
    out = await analytics_service.flaky_tests(_db(auto, manual), "proj", 30, 3)
    assert len(out["items"]) == 3
    assert [i["source"] for i in out["items"]] == ["auto", "auto", "manual"]
