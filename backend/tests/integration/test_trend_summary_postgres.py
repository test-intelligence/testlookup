"""W08 TRND-06 on real PostgreSQL: the server's trend summary is the page's derivation over the page's reads.

A project with runs on 10 of the last 14 UTC days and a 4-day silence between
them (and a suite-less group, so tag quality is not trivially 100). Through the
real app:

* ``GET /api/v1/analytics/trend-summary`` equals ``summarize()`` over the
  answers of ``GET /api/v1/metrics/trends`` and ``GET /api/v1/analytics/coverage``
  for the same scope -- the gap, the verdict, the executions, every key;
* the same holds with a suite filter (both reads receive it);
* a project the caller cannot read is the empty ``measured: false`` body.

Uses ``tests/integration/defect_w06_world.py``. Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head and ``REDIS_URL``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.integration.defect_w06_world import defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

#: Days ago with runs; 7, 6, 5 and 4 days ago are silent (the 4-day gap).
ACTIVE_DAYS_AGO = (13, 12, 11, 10, 9, 8, 3, 2, 1, 0)


def _at(days_ago: int) -> datetime:
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    return min(midnight + timedelta(hours=1), now - timedelta(minutes=1))


@pytest.fixture
async def world():
    from app.models.postgres import LaunchStatus, TestCase, TestRun

    async with defect_world() as w:
        async with w.sessions.begin() as db:
            w.X = await w.project(db)
            for days_ago in ACTIVE_DAYS_AGO:
                at = _at(days_ago)
                # Older days pass more: the recent half is worse (a declining trend).
                checkout_failed = 0 if days_ago >= 8 else 3
                for suite, passed, failed, skipped in (
                    ("checkout", 6, checkout_failed, 1), ("payments", 4, 1, 0), (None, 2, 0, 0),
                ):
                    run_id = uuid.uuid4()
                    db.add(TestRun(
                        id=run_id, project_id=w.X, build_number=f"trnd06-{days_ago}-{suite}", jenkins_job="trnd06",
                        status=LaunchStatus.FAILED if failed else LaunchStatus.PASSED, ingestion_source="unknown",
                        total_tests=passed + failed + skipped, passed_tests=passed, failed_tests=failed,
                        skipped_tests=skipped, broken_tests=0, primary_suite_name=suite, created_at=at,
                    ))
                    await db.flush()
                    for status, n in (("PASSED", passed), ("FAILED", failed), ("SKIPPED", skipped)):
                        for i in range(n):
                            db.add(TestCase(
                                id=uuid.uuid4(), test_run_id=run_id, test_name=f"test_{suite}_{status}_{i}",
                                suite_name=suite, status=status, test_fingerprint=f"{suite}-{status}-{i}",
                                created_at=at,
                            ))
        yield w


async def _get(w, path: str, headers=None, **params) -> dict:
    response = await w.client.get(path, params=params, headers=headers or w.admin)
    assert response.status_code == 200, response.text
    return response.json()


async def _parity(w, **params) -> dict:
    from app.services.trend_summary_service import summarize, utc_today

    trends = await _get(w, "/api/v1/metrics/trends", **params)
    coverage = await _get(w, "/api/v1/analytics/coverage", **params)
    summary = await _get(w, "/api/v1/analytics/trend-summary", **params)
    meta = summary.pop("meta")
    assert meta["scope"]["projects"][0]["id"] == str(w.X)
    expected = summarize(trends["data"], coverage["suites"], days=14, today=utc_today())
    assert summary == expected
    return summary


async def test_the_summary_is_the_derivation_over_the_pages_own_reads(world):
    w = world
    summary = await _parity(w, project_id=str(w.X), days=14)
    assert summary["days_with_runs"] == 10 and summary["empty_days"] == 4
    gap = summary["gap"]
    assert (gap["silent_days"], gap["ongoing"], gap["expected_runs_missed"]) == (4, False, 4)
    today = datetime.now(timezone.utc).date()
    assert (gap["start"], gap["end"]) == ((today - timedelta(days=7)).isoformat(), (today - timedelta(days=4)).isoformat())
    per_day = 6 + 1 + 4 + 1 + 2  # checkout passed+skipped, payments, the suite-less run
    assert summary["executions"]["total"] == 10 * per_day + 4 * 3  # + checkout's failures on the last 4 days
    assert summary["executions"]["skipped"] == 10
    assert 0 < summary["untagged_share"] < 1, "the suite-less group lowers tag quality"
    assert summary["confidence"]["verdict"] in ("DECLINING", "MIXED", "HEALTHY", "INSUFFICIENT")
    assert summary["pass_rate"] == round(
        summary["executions"]["passed"] / summary["executions"]["evaluated"] * 100, 1,
    )


async def test_a_suite_filter_reaches_both_reads(world):
    w = world
    summary = await _parity(w, project_id=str(w.X), days=14, suite_name="checkout")
    assert summary["executions"]["total"] == 10 * 7 + 4 * 3
    assert summary["untagged_share"] == 0.0


async def test_a_project_the_caller_cannot_read_is_the_empty_body(world):
    w = world
    summary = await _get(w, "/api/v1/analytics/trend-summary", headers=w.outsider, project_id=str(w.X), days=14)
    assert summary["measured"] is False and summary["executions"]["total"] == 0
    assert summary["meta"]["scope"]["projects"] == []
