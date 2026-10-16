"""FAIL-02 on real PostgreSQL: the median time from a test's first failure to its next pass (D02).

``analytics_service.time_to_fix`` is one window-function statement over
``test_case_history``. Every fixture below is a per-test sequence of
executions; the expectations are the episode rule written out by hand:

* an episode opens at a FAILED/BROKEN execution that follows a pass (or is the
  test's first in the window) and closes at the next PASSED one;
* SKIPPED neither opens nor closes; an episode still open is ``open``;
* a test with 3+ episodes oscillates and is left out (counted); a test flagged
  flaky -- a LIVE quarantine request or a human FLAKY_TEST triage -- too;
* a test is (project, fingerprint): the same fingerprint in two projects is two.

Uses ``tests/integration/defect_w06_world.py`` (projects P / Q, a member of P,
an outsider of Q, the app over ASGI). Each case writes its own project, so the
cases never see each other's rows. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``
migrated to head.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from tests.integration.defect_w06_world import StatementCounter, defect_world, scope

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

#: Every sequence starts here: inside a 30-day window, with room before it.
T0 = datetime.now(timezone.utc) - timedelta(days=20)


@pytest.fixture
async def world():
    async with defect_world() as w:
        yield w


async def _history(w, db, project_id, name, events, *, suite="checkout-api", release_id=None):
    """``events``: ``(status, hours after T0)`` per execution, one run each.
    Returns the test's fingerprint (``<project>-<name>`` is NOT used: the same
    name in two projects deliberately shares one fingerprint)."""
    from app.models.postgres import LaunchStatus, TestCase, TestCaseHistory, TestRun

    fp = f"ttf-{w.tag}-{name}"
    for status, hours in events:
        at = T0 + timedelta(hours=hours)
        run_id, case_id = uuid.uuid4(), uuid.uuid4()
        db.add(TestRun(id=run_id, project_id=project_id, build_number=f"ttf-{uuid.uuid4().hex[:8]}",
                       status=LaunchStatus.FAILED, created_at=at, start_time=at,
                       primary_release_id=release_id, primary_suite_name=suite))
        await db.flush()
        db.add(TestCase(id=case_id, test_run_id=run_id, test_name=name, suite_name=suite,
                        test_fingerprint=fp, status=status, created_at=at))
        await db.flush()
        db.add(TestCaseHistory(test_case_id=case_id, test_run_id=run_id, test_fingerprint=fp,
                               status=status, created_at=at))
    await db.flush()
    return fp


async def _fixes(w, db, project_id, hours, *, prefix="fix"):
    """One closed episode per entry: F at 0, P ``h`` hours later."""
    for i, h in enumerate(hours):
        await _history(w, db, project_id, f"{prefix}{i}", [("PASSED", -1), ("FAILED", 0), ("PASSED", h)])


async def _ttf(w, project_id=None, days=30, **kw):
    from app.services import analytics_service

    async with w.sessions() as db:
        return await analytics_service.time_to_fix(db, scope(project_id, days, **kw))


# ── 1-3. the episode rule ───────────────────────────────────────────────────


async def test_p_f_f_p_is_one_episode_closed_after_48_hours(world):
    async with world.sessions.begin() as db:
        p = await world.project(db)
        for i in range(5):
            await _history(world, db, p, f"t{i}", [("PASSED", 0), ("FAILED", 24), ("FAILED", 48), ("PASSED", 72)])
    out = await _ttf(world, p)
    assert (out["measured"], out["fixed"], out["open"]) == (True, 5, 0)
    assert out["median_hours"] == 48.0 and out["mean_hours"] == 48.0


async def test_an_episode_open_at_the_window_start_counts_from_its_first_failure_inside_it(world):
    async with world.sessions.begin() as db:
        p = await world.project(db)
        for i in range(5):
            # A failure 40 days before T0 (outside the 30-day window), then
            # F at T0 and P a day later: 24 h, not 40 days.
            await _history(world, db, p, f"t{i}", [("FAILED", -40 * 24), ("FAILED", 0), ("PASSED", 24)])
    out = await _ttf(world, p)
    assert out["fixed"] == 5 and out["median_hours"] == 24.0


async def test_an_episode_still_open_is_counted_and_not_in_the_median(world):
    async with world.sessions.begin() as db:
        p = await world.project(db)
        await _fixes(world, db, p, [10, 10, 10, 10, 10])
        await _history(world, db, p, "still_red", [("PASSED", 0), ("FAILED", 1), ("FAILED", 300)])
    out = await _ttf(world, p)
    assert (out["fixed"], out["open"]) == (5, 1)
    assert out["median_hours"] == 10.0 and out["mean_hours"] == 10.0


async def test_skipped_neither_opens_nor_closes_an_episode(world):
    async with world.sessions.begin() as db:
        p = await world.project(db)
        for i in range(5):
            await _history(world, db, p, f"t{i}", [("SKIPPED", 0), ("FAILED", 10), ("SKIPPED", 20), ("PASSED", 30)])
    out = await _ttf(world, p)
    assert (out["fixed"], out["open"], out["median_hours"]) == (5, 0, 20.0)


# ── 4-5. exclusions ─────────────────────────────────────────────────────────


async def test_three_episodes_oscillate_and_are_left_out_and_counted(world):
    async with world.sessions.begin() as db:
        p = await world.project(db)
        await _fixes(world, db, p, [5, 5, 5, 5, 5])
        await _history(world, db, p, "osc", [(s, h) for h, s in enumerate(
            ["PASSED", "FAILED", "PASSED", "FAILED", "PASSED", "FAILED", "PASSED"])])
        # Two episodes are still a test that broke twice: kept.
        await _history(world, db, p, "twice", [("FAILED", 0), ("PASSED", 5), ("FAILED", 6), ("PASSED", 11)])
    out = await _ttf(world, p)
    assert out["excluded_oscillating_tests"] == 1
    assert out["fixed"] == 7 and out["median_hours"] == 5.0


async def test_a_live_quarantine_or_a_human_flaky_triage_excludes_a_released_one_does_not(world):
    from app.models.postgres import FlakyQuarantineRequest

    async with world.sessions.begin() as db:
        p = await world.project(db)
        await _fixes(world, db, p, [1, 1, 1, 1, 1])
        live = await _history(world, db, p, "quarantined", [("FAILED", 0), ("PASSED", 500)])
        released = await _history(world, db, p, "released", [("FAILED", 0), ("PASSED", 1)])
        triaged = await _history(world, db, p, "triaged", [("FAILED", 0), ("PASSED", 500)])
        db.add(FlakyQuarantineRequest(project_id=p, test_fingerprint=live, status="QUARANTINED"))
        db.add(FlakyQuarantineRequest(project_id=p, test_fingerprint=released, status="RELEASED"))
        await db.execute(
            text("UPDATE test_cases SET triage_status = 'FLAKY_TEST', triage_updated_at = :at "
                 "WHERE test_fingerprint = :fp"),
            {"at": T0 + timedelta(days=1), "fp": triaged},
        )
        # Another project quarantining the same fingerprint flags nothing here.
        q = await world.project(db)
        db.add(FlakyQuarantineRequest(project_id=q, test_fingerprint=f"ttf-{world.tag}-fix0", status="QUARANTINED"))
    out = await _ttf(world, p)
    assert out["excluded_flaky_tests"] == 2
    assert out["fixed"] == 6, "five fixes + the RELEASED quarantine's fix; fix0 is not flagged by Q"
    assert out["median_hours"] == 1.0


# ── 7. scope ────────────────────────────────────────────────────────────────


async def test_the_same_fingerprint_in_two_projects_is_two_tests(world):
    async with world.sessions.begin() as db:
        p, q = await world.project(db), await world.project(db)
        for i in range(4):
            await _history(world, db, p, f"own{i}", [("FAILED", 0), ("PASSED", 2)])
        # Interleaved: merged by fingerprint alone these read F F P P -- one
        # episode -- where they are two fixes, one per project.
        await _history(world, db, p, "shared", [("FAILED", 0), ("PASSED", 24)])
        await _history(world, db, q, "shared", [("FAILED", 12), ("PASSED", 36)])
    both = await _ttf(world, None, allowed=frozenset({p, q}))
    assert both["fixed"] == 6
    only_p = await _ttf(world, p)
    assert only_p["fixed"] == 5 and only_p["median_hours"] == 2.0


async def test_release_and_suite_filters_apply(world):
    from app.models.postgres import Release

    async with world.sessions.begin() as db:
        p = await world.project(db)
        rel = uuid.uuid4()
        db.add(Release(id=rel, project_id=p, name=f"ttf-{world.tag}", version="1.0.0", status="active"))
        await db.flush()
        for i in range(5):
            await _history(world, db, p, f"in{i}", [("FAILED", 0), ("PASSED", 3)], release_id=rel)
            await _history(world, db, p, f"out{i}", [("FAILED", 0), ("PASSED", 50)], suite="search", release_id=rel)
        await _history(world, db, p, "norel", [("FAILED", 0), ("PASSED", 70)])
    everything = await _ttf(world, p)
    assert everything["fixed"] == 11
    by_release = await _ttf(world, p, releases=(rel,))
    assert by_release["fixed"] == 10
    by_both = await _ttf(world, p, releases=(rel,), suites=("checkout-api",))
    assert (by_both["fixed"], by_both["median_hours"]) == (5, 3.0)


async def test_the_route_scopes_validates_and_states_the_reason(world):
    # The world's member reads P only; Q is the outsider's.
    path = "/api/v1/analytics/time-to-fix"
    async with world.sessions.begin() as db:
        await _fixes(world, db, world.P, [1, 2, 3, 4])
    ok = await world.client.get(path, params={"project_id": str(world.P)}, headers=world.member)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert set(body) == {"measured", "reason", "median_hours", "mean_hours", "fixed", "open",
                         "excluded_flaky_tests", "excluded_oscillating_tests", "min_fixes", "window_days", "meta"}
    assert (body["measured"], body["median_hours"], body["fixed"], body["window_days"]) == (False, None, 4, 30)
    assert body["reason"] == "Fewer than 5 tests were fixed in the last 30 days (4 fixed)."
    assert body["meta"]["measured"] is False and body["meta"]["reason"] == body["reason"]

    foreign = await world.client.get(path, params={"project_id": str(world.Q)}, headers=world.member)
    assert foreign.status_code == 403, foreign.text
    too_long = await world.client.get(path, params={"project_id": str(world.P), "days": 91}, headers=world.member)
    assert too_long.status_code == 422, too_long.text
    ninety = await world.client.get(path, params={"project_id": str(world.P), "days": 90}, headers=world.member)
    assert ninety.status_code == 200, ninety.text


# ── 8-9. the floor, median vs mean, one statement ───────────────────────────


async def test_four_fixes_are_not_measured_five_are(world):
    async with world.sessions.begin() as db:
        p = await world.project(db)
        await _fixes(world, db, p, [1, 2, 3, 4])
    four = await _ttf(world, p)
    assert (four["measured"], four["median_hours"], four["mean_hours"], four["fixed"]) == (False, None, None, 4)
    assert four["reason"] == "Fewer than 5 tests were fixed in the last 30 days (4 fixed)."
    async with world.sessions.begin() as db:
        await _fixes(world, db, p, [5], prefix="fifth")
    five = await _ttf(world, p)
    assert (five["measured"], five["reason"], five["fixed"]) == (True, None, 5)


async def test_the_median_is_not_the_mean(world):
    async with world.sessions.begin() as db:
        p = await world.project(db)
        await _fixes(world, db, p, [1, 2, 3, 4, 100])
    out = await _ttf(world, p)
    assert (out["median_hours"], out["mean_hours"]) == (3.0, 22.0)


async def test_one_statement_at_90_days(world):
    from app.services import analytics_service

    async with world.sessions() as db:
        await db.execute(text("SELECT 1"))
        with StatementCounter(world.engine) as counter:
            await analytics_service.time_to_fix(db, scope(world.P, 90))
    assert len(counter.statements) == 1, counter.statements
