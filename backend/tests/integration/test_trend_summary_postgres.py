"""W08 TRND-06 on real PostgreSQL: the server's trend summary is the page's derivation over the page's reads.

A project with runs on 10 of the last 14 UTC days and a 4-day silence between
them (and a suite-less group, so tag quality is not trivially 100). Through the
real app:

* ``GET /api/v1/analytics/trend-summary`` equals ``summarize()`` over the
  answers of ``GET /api/v1/metrics/trends`` and ``GET /api/v1/analytics/coverage``
  for the same scope -- the gap, the verdict, the executions, every key;
* the same holds with a suite filter (both reads receive it);
* a project the caller cannot read is the empty ``measured: false`` body.

**One clock for the seed and the server.** The seed places runs by "days
ago", and each of the server's reads takes "today" from its own clock call
(the summary's ``utc_today``, the trend's window, the coverage window). The
world therefore runs on one Python clock (freezegun, ticking): noon UTC by
default, half a day from either midnight. CI run 43122547657 reached this
module at 00:00:34 UTC, when the seed's "a minute before now" fell on the
previous day: today's runs landed on yesterday and the page counted 9 days with
runs, not 10. :func:`test_the_first_and_last_minute_of_a_utc_day_count_the_same_days`
runs the world at both edges of a day on purpose.

Uses ``tests/integration/defect_w06_world.py``. Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head and ``REDIS_URL``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone

import pytest

from tests.integration.defect_w06_world import defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

#: Days ago with runs; 7, 6, 5 and 4 days ago are silent (the 4-day gap).
ACTIVE_DAYS_AGO = (13, 12, 11, 10, 9, 8, 3, 2, 1, 0)
#: The world's time of day (UTC) unless a test asks for another.
NOON = time(12, 0)


def _at(now: datetime, days_ago: int) -> datetime:
    """A seed instant on the UTC day ``days_ago`` before ``now``'s: 01:00, or a
    minute before ``now`` while 01:00 is still ahead -- and never before that
    day's midnight, which "a minute before now" is in a day's first minute."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    return max(midnight, min(midnight + timedelta(hours=1), now - timedelta(minutes=1)))


@pytest.fixture
async def world(request):
    """The seeded project, on a Python clock frozen (and ticking) at today's
    ``NOON`` UTC, or at the time of day a test passes indirectly."""
    from app.models.postgres import LaunchStatus, TestCase, TestRun
    from tests.integration.midnight_replay import ticking_clock

    clock = getattr(request, "param", NOON)
    instant = datetime.combine(datetime.now(timezone.utc).date(), clock, tzinfo=timezone.utc)
    with ticking_clock(instant):
        async with defect_world() as w:
            w.now = datetime.now(timezone.utc)
            async with w.sessions.begin() as db:
                w.X = await w.project(db)
                for days_ago in ACTIVE_DAYS_AGO:
                    at = _at(w.now, days_ago)
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


def _assert_the_seeded_days(w, summary: dict) -> None:
    """Ten days with runs and the 4-day silence, 7 to 4 days before the world's day."""
    assert summary["days_with_runs"] == 10 and summary["empty_days"] == 4
    gap = summary["gap"]
    assert (gap["silent_days"], gap["ongoing"], gap["expected_runs_missed"]) == (4, False, 4)
    today = w.now.date()
    assert (gap["start"], gap["end"]) == ((today - timedelta(days=7)).isoformat(), (today - timedelta(days=4)).isoformat())


async def test_the_summary_is_the_derivation_over_the_pages_own_reads(world):
    w = world
    summary = await _parity(w, project_id=str(w.X), days=14)
    _assert_the_seeded_days(w, summary)
    per_day = 6 + 1 + 4 + 1 + 2  # checkout passed+skipped, payments, the suite-less run
    assert summary["executions"]["total"] == 10 * per_day + 4 * 3  # + checkout's failures on the last 4 days
    assert summary["executions"]["skipped"] == 10
    assert 0 < summary["untagged_share"] < 1, "the suite-less group lowers tag quality"
    assert summary["confidence"]["verdict"] in ("DECLINING", "MIXED", "HEALTHY", "INSUFFICIENT")
    assert summary["pass_rate"] == round(
        summary["executions"]["passed"] / summary["executions"]["evaluated"] * 100, 1,
    )


@pytest.mark.parametrize(
    "world", [time(0, 0, 30), time(23, 59)], indirect=True,
    ids=["first-minute-of-the-day", "last-minute-of-the-day"],
)
async def test_the_first_and_last_minute_of_a_utc_day_count_the_same_days(world):
    """CI run 43122547657 reached this module at 00:00:34 UTC: the seed's "a
    minute before now" was the previous day, today's runs shared it with
    yesterday's, and the page counted 9 days with runs. Seed and read 30
    seconds after midnight on purpose, and a minute before the next one: the
    same ten days, the same silence, the same derivation."""
    w = world
    assert w.now.date() == datetime.now(timezone.utc).date(), "the reads share the seed's day"
    summary = await _parity(w, project_id=str(w.X), days=14)
    _assert_the_seeded_days(w, summary)


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
