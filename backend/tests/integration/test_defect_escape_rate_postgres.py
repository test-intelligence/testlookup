"""DEF-03 on real PostgreSQL: the escape rate and the intake's release check.

An escape is a defect filed AFTER a release it affects had shipped (owner
decision D01). "Affects" is the gate's rule: asserted impact, else where it was
found; JSON ``null`` and ``[]`` take the fallback. Only releases of the
defect's own project count, and only shipped ones. Below five attributed
defects the rate is not measured (``rate`` null, never 0) and says why.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (migrated to head) and ``REDIS_URL``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from tests.integration.defect_w06_world import defect_world, scope

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime.now(timezone.utc)


def _ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


@pytest.fixture
async def world():
    async with defect_world() as w:
        async with w.sessions.begin() as db:
            w.esc = {
                # R shipped 10 days ago (the world).
                "before_ship": await w.defect(db, affects_releases=[str(w.R)], created_at=_ago(12)),
                "after_ship": await w.defect(db, affects_releases=[str(w.R)], created_at=_ago(3)),
                "found_in_r": await w.defect(db, release_id=w.R, created_at=_ago(2)),
                "json_null": await w.defect(db, release_id=w.R, affects_releases=None, created_at=_ago(1)),
                "empty_list": await w.defect(db, release_id=w.R, affects_releases=[], created_at=_ago(1)),
                "unshipped": await w.defect(db, affects_releases=[str(w.R2)], created_at=_ago(1)),
                "foreign_release": await w.defect(db, affects_releases=[str(w.RQ)], created_at=_ago(1)),
                "outside_window": await w.defect(db, affects_releases=[str(w.R)], created_at=_ago(45)),
            }
        yield w


async def _rate(w, project_id=None, days=30, releases=()):
    from app.services import analytics_service

    async with w.sessions() as db:
        return await analytics_service.defect_escape_rate(db, scope(project_id or w.P, days, releases=releases))


async def test_the_rate_counts_escapes_among_defects_affecting_a_shipped_release(world):
    payload = await _rate(world)
    assert payload["in_window"] == 7
    assert payload["attributed"] == 5  # before_ship, after_ship, found_in_r, json_null, empty_list
    assert payload["escaped"] == 4
    assert payload["measured"] is True and payload["reason"] is None
    assert payload["rate"] == 80.0
    assert payload["sample_floor"] == 5 and payload["window_days"] == 30


async def test_below_the_floor_it_is_not_measured_and_says_why(world):
    from app.models.postgres import Defect

    async with world.sessions.begin() as db:
        row = await db.get(Defect, world.esc["empty_list"])
        await db.delete(row)
    payload = await _rate(world)
    assert payload["attributed"] == 4
    assert payload["measured"] is False and payload["rate"] is None
    assert payload["reason"] == (
        "4 defect(s) reported in the last 30 days affect a release that has shipped; 5 are needed."
    )


async def test_a_defect_affecting_two_shipped_releases_counts_once(world):
    from app.models.postgres import Release

    async with world.sessions.begin() as db:
        r3 = Release(project_id=world.P, name=f"w06a-r3-{world.tag}", version="2.3.0", status="released",
                     released_at=_ago(20))
        db.add(r3)
        await db.flush()
        await world.defect(db, affects_releases=sorted([str(world.R), str(r3.id)]), created_at=_ago(15))
    payload = await _rate(world)
    # Filed after r3 shipped (20 days ago) but before R (10): an escape, counted once.
    assert payload["attributed"] == 6 and payload["escaped"] == 5


async def test_a_selected_release_narrows_and_the_sentinel_measures_nothing(world):
    only_r = await _rate(world, releases=[world.R])
    assert only_r["attributed"] == 5
    other = await _rate(world, releases=[world.R2])
    assert other["attributed"] == 0 and other["measured"] is False
    sentinel = await _rate(world, releases=["unattributed"])
    assert sentinel["attributed"] == 0 and sentinel["rate"] is None
    assert sentinel["reason"] == 'Escapes are counted against shipped releases; "Unattributed" has none.'


async def test_no_defect_in_the_window_has_its_own_reason(world):
    async with world.sessions.begin() as db:
        empty = await world.project(db)
    payload = await _rate(world, project_id=empty)
    assert payload["in_window"] == 0 and payload["rate"] is None
    assert payload["reason"] == "No defects were reported in the last 30 days."


async def test_outcomes_marked_in_the_window_are_the_caption(world):
    from app.models.postgres import ReleaseOutcome

    async with world.sessions.begin() as db:
        db.add(ReleaseOutcome(release_id=world.R, project_id=world.P, outcome_kind="incident",
                              reason="checkout down", marked_at=_ago(2)))
        db.add(ReleaseOutcome(release_id=world.R, project_id=world.P, outcome_kind="rollback",
                              reason="rolled back long ago", marked_at=_ago(40)))
        db.add(ReleaseOutcome(release_id=world.RQ, project_id=world.Q, outcome_kind="incident",
                              reason="another project"))
    payload = await _rate(world)
    assert payload["outcomes"] == {"incidents": 1, "rollbacks": 0, "releases": 1}


async def test_the_route_authorises_and_declares_the_ignored_suite(world):
    path = "/api/v1/analytics/defects/escape-rate"
    ok = await world.client.get(path, params={"project_id": str(world.P), "suite_name": "x"}, headers=world.member)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["rate"] == 80.0
    assert {"dimension": "suite", "reason": "The escape rate is counted per release, not per suite."} in (
        body["meta"]["ignored_filters"]
    )
    assert body["meta"]["measured"] is True
    foreign = await world.client.get(path, params={"project_id": str(world.Q)}, headers=world.member)
    assert foreign.status_code == 403, foreign.text
    too_long = await world.client.get(path, params={"project_id": str(world.P), "days": 366}, headers=world.member)
    assert too_long.status_code == 422, too_long.text


async def test_intake_refuses_another_project_s_release_and_normalises_ids(world):
    from app.models.postgres import Defect
    from app.services import analytics_service

    payload = {"title": "Refund posts twice", "severity": "P1"}
    async with world.sessions() as db:
        before = (await db.execute(select(Defect.id).where(Defect.project_id == world.P))).scalars().all()
        for bad in ([str(world.RQ)], ["not-a-uuid"], [str(world.R), str(world.RQ)]):
            with pytest.raises(HTTPException) as err:
                await analytics_service.create_manual_defect(db, world.P, {**payload, "affects_releases": bad})
            assert err.value.status_code == 422
            assert err.value.detail == "One or more of these releases are not in this project."
        await db.rollback()
        after = (await db.execute(select(Defect.id).where(Defect.project_id == world.P))).scalars().all()
        assert sorted(after) == sorted(before), "nothing stored"

    async with world.sessions.begin() as db:
        created = await analytics_service.create_manual_defect(
            db, world.P, {**payload, "affects_releases": [str(world.R).upper(), str(world.R)]},
        )
    assert created.affects_releases == [str(world.R).lower()]
    # Filed now, after R shipped: the sixth attributed defect, an escape.
    rate = await _rate(world)
    assert (rate["attributed"], rate["escaped"]) == (6, 5)


async def test_the_demo_seed_reads_seven_of_nine_and_re_runs_in_place(world):
    """§7.3 step 0: a project whose only attributed defects are the seed's reads 77.8 % (7 of 9) for 30 d."""
    from app.models.postgres import Project, Release
    from app.services import analytics_service
    from scripts.seed_defect_history import apply_defect_history

    async with world.sessions.begin() as db:
        pid = await world.project(db)
        db.add(Release(project_id=pid, name=f"w06a-seed-{world.tag}", version="1.0.0", status="released",
                       released_at=_ago(10)))
        await db.flush()
        first = await apply_defect_history(db, await db.get(Project, pid))
    async with world.sessions.begin() as db:
        again = await apply_defect_history(db, await db.get(Project, pid))
    assert first["deleted"] == 0 and again["deleted"] == 21, "a re-run replaces only its own rows"

    payload = await _rate(world, project_id=pid)
    assert (payload["escaped"], payload["attributed"], payload["rate"]) == (7, 9, 77.8)
    async with world.sessions() as db:
        trend = await analytics_service.defect_trend(db, scope(pid, 30))
    assert trend["totals"]["open"] == trend["points"][-1]["open_backlog"] == 9
    assert trend["totals"]["closed_without_time"] == 0, "every seeded close has a time"
    assert trend["totals"]["resolved_in_window"] >= 8
