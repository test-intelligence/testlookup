"""DEF-04 on real PostgreSQL: the defect trend is reconstructed from the defects table.

Every expectation is computed by an independent Python model of "open at the
end of day d" (created on or before d, not closed on or before d) over the
same fixtures, so the SQL is checked against the rule rather than against
itself. The fixtures cover: open before the window, opened and resolved inside
it, opened and resolved the same day, a re-opened defect, a ``resolved_at``
before ``created_at`` (clamps), a live row that still carries a stale
``resolved_at``, and a legacy INVALID row with no ``resolved_at`` (in no
point, counted in ``closed_without_time``). Plus the priority parity: the SQL
``priority_sql`` and the Python ``priority_of`` agree on the shared case table.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (migrated to head) and ``REDIS_URL``.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import text

from tests.integration.defect_w06_world import StatementCounter, defect_world, scope

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

TODAY = datetime.now(timezone.utc).date()


def _at(days_ago: int, hour: int = 12) -> datetime:
    return datetime.combine(TODAY - timedelta(days=days_ago), time(hour), tzinfo=timezone.utc)


#: name -> (status, created days ago, resolved days ago or None, severity)
FIXTURES = {
    "before_window_open": ("OPEN", 40, None, "CRITICAL"),
    "opened_then_resolved": ("RESOLVED", 10, 5, "HIGH"),
    "same_day": ("RESOLVED", 3, 3, "LOW"),
    "resolved_before_created": ("RESOLVED", 20, 25, "LOW"),
    "legacy_invalid_no_time": ("INVALID", 8, None, "CRITICAL"),
    "live_with_stale_resolved_at": ("OPEN", 12, 2, "LOW"),
    "reopened": ("OPEN", 15, None, "HIGH"),
    "invalid_closed": ("INVALID", 6, 4, "LOW"),
    "in_progress": ("IN_PROGRESS", 1, None, "HIGH"),
}


def _created(name):
    return _at(FIXTURES[name][1], 9)


def _resolved(name):
    status, created, resolved, _ = FIXTURES[name]
    if resolved is None:
        return None
    # Same day: six hours after creation, so a fraction of a day to resolve.
    return _at(resolved, 15) if resolved != created else _created(name) + timedelta(hours=6)


def _model(days: int):
    """The expected points and totals, from the rule (not from the SQL)."""
    d0 = TODAY - timedelta(days=days - 1)
    rows = []
    for name, (status, _c, _r, severity) in FIXTURES.items():
        live = status in ("OPEN", "IN_PROGRESS")
        resolved = _resolved(name)
        if not live and resolved is None:
            continue  # cannot be placed on a day
        created_day = _created(name).date()
        closed_day = None if live else max(resolved, _created(name)).date()
        resolve_days = None
        if status == "RESOLVED":
            resolve_days = (max(resolved, _created(name)) - _created(name)).total_seconds() / 86400
        rows.append((created_day, closed_day, severity in ("CRITICAL", "HIGH"), resolve_days))
    points = []
    for i in range(days):
        day = d0 + timedelta(days=i)
        open_rows = [r for r in rows if r[0] <= day and (r[1] is None or r[1] > day)]
        points.append({
            "date": day.isoformat(),
            "opened": sum(1 for r in rows if r[0] == day),
            "closed": sum(1 for r in rows if r[1] == day),
            "open_backlog": len(open_rows),
            "p0p1_open": sum(1 for r in open_rows if r[2]),
            "resolved": [r[3] for r in rows if r[1] == day and r[3] is not None],
        })
    for i, point in enumerate(points):
        trailing = [v for p in points[max(0, i - 6):i + 1] for v in p["resolved"]]
        point["mttr_days"] = round(sum(trailing) / len(trailing), 2) if trailing else None
    return points


@pytest.fixture
async def world():
    async with defect_world() as w:
        async with w.sessions.begin() as db:
            w.trend_ids = {}
            for name, (status, _c, _r, severity) in FIXTURES.items():
                w.trend_ids[name] = await w.defect(
                    db, resolution_status=status, severity=severity,
                    created_at=_created(name), resolved_at=_resolved(name),
                )
        yield w


async def _trend(w, days=30, **kwargs):
    from app.services import analytics_service

    async with w.sessions() as db:
        return await analytics_service.defect_trend(db, scope(w.P, days), today=TODAY, **kwargs)


@pytest.mark.parametrize("days", [7, 30, 90])
async def test_points_match_the_open_at_end_of_day_model(world, days):
    payload = await _trend(world, days)
    expected = _model(days)
    assert payload["window_days"] == days
    assert [p["date"] for p in payload["points"]] == [p["date"] for p in expected]
    assert payload["points"][-1]["date"] == TODAY.isoformat()
    for got, want in zip(payload["points"], expected):
        assert (got["opened"], got["closed"], got["open_backlog"], got["p0p1_open"]) == (
            want["opened"], want["closed"], want["open_backlog"], want["p0p1_open"],
        ), got["date"]
        assert got["mttr_days"] == want["mttr_days"], got["date"]


async def test_the_last_point_s_backlog_is_the_open_total(world):
    payload = await _trend(world, 30)
    totals = payload["totals"]
    assert payload["points"][-1]["open_backlog"] == totals["open"] == 4
    assert (totals["p0_open"], totals["p1_open"]) == (1, 2)
    assert totals["closed_without_time"] == 1


async def test_a_legacy_close_without_a_time_is_in_no_point(world):
    payload = await _trend(world, 30)
    created_day = _created("legacy_invalid_no_time").date().isoformat()
    (point,) = [p for p in payload["points"] if p["date"] == created_day]
    model = {p["date"]: p for p in _model(30)}
    assert point["opened"] == model[created_day]["opened"]


async def test_window_totals(world):
    totals = (await _trend(world, 30))["totals"]
    points = _model(30)
    resolved = [v for p in points for v in p["resolved"]]
    assert totals["resolved_in_window"] == len(resolved) == 3
    assert totals["mttr_days"] == round(sum(resolved) / len(resolved), 2)
    assert totals["opened_in_window"] == sum(p["opened"] for p in points)
    assert totals["closed_in_window"] == sum(p["closed"] for p in points)
    last7 = points[-7:]
    assert totals["opened_last_7d"] == sum(p["opened"] for p in last7)
    assert totals["closed_last_7d"] == sum(p["closed"] for p in last7)
    assert totals["net_last_7d"] == totals["opened_last_7d"] - totals["closed_last_7d"]


async def test_nothing_resolved_is_none_not_zero(world):
    async with world.sessions.begin() as db:
        empty = await world.project(db)
        await world.defect(db, project_id=empty, created_at=_at(3))
    from app.services import analytics_service

    async with world.sessions() as db:
        payload = await analytics_service.defect_trend(db, scope(empty, 7), today=TODAY)
    assert payload["totals"]["mttr_days"] is None
    assert all(p["mttr_days"] is None for p in payload["points"])
    assert payload["totals"]["open"] == payload["points"][-1]["open_backlog"] == 1


async def test_a_re_open_through_apply_status_is_open_again(world):
    from app.models.postgres import Defect
    from app.services.defect_status import apply_status

    async with world.sessions.begin() as db:
        row = await db.get(Defect, world.trend_ids["opened_then_resolved"])
        assert apply_status(row, "OPEN", now=datetime.now(timezone.utc))
    totals = (await _trend(world, 30))["totals"]
    assert totals["open"] == 5
    assert (await _trend(world, 30))["points"][-1]["open_backlog"] == 5


async def test_two_statements_for_any_window(world):
    from app.services import analytics_service

    for days in (7, 365):
        async with world.sessions() as db:
            await db.execute(text("SELECT 1"))  # check the connection out before counting
            with StatementCounter(world.engine) as counter:
                await analytics_service.defect_trend(db, scope(world.P, days), today=TODAY)
        assert len(counter.statements) == 2, counter.statements


async def test_the_route_scopes_and_validates(world):
    path = "/api/v1/analytics/defects/trend"
    ok = await world.client.get(path, params={"project_id": str(world.P), "days": 7}, headers=world.member)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert len(body["points"]) == 7 and body["window_days"] == 7 and "meta" in body
    assert body["points"][-1]["open_backlog"] == body["totals"]["open"]

    foreign = await world.client.get(path, params={"project_id": str(world.Q)}, headers=world.member)
    assert foreign.status_code == 403, foreign.text
    too_long = await world.client.get(path, params={"project_id": str(world.P), "days": 366}, headers=world.member)
    assert too_long.status_code == 422, too_long.text
    bogus = await world.client.get(path, params={"project_id": str(world.P), "release_match": "x"}, headers=world.member)
    assert bogus.status_code == 422, bogus.text

    # All projects for a member: P and the soft-deleted D are theirs; D never counts.
    async with world.sessions.begin() as db:
        await world.defect(db, project_id=world.D, created_at=_at(2))
        await world.defect(db, project_id=world.Q, created_at=_at(2))
    every = await world.client.get(path, params={"days": 30}, headers=world.member)
    assert every.status_code == 200, every.text
    assert every.json()["totals"]["open"] == body["totals"]["open"] == 4


async def test_priority_sql_agrees_with_priority_of_on_the_shared_table(world):
    from app.services.defect_status import priority_of, priority_sql
    from tests.test_defect_priority import CASES

    async with world.sessions.begin() as db:
        table = await world.project(db)
        ids = []
        for severity, category, confidence, _expected in CASES:
            ids.append(await world.defect(
                db, project_id=table, severity=severity, failure_category=category, ai_confidence_score=confidence,
            ))
    async with world.sessions() as db:
        got = dict((await db.execute(
            text(f"SELECT d.id, {priority_sql('d')} FROM defects d WHERE d.project_id = :p"), {"p": table},
        )).all())
    for did, (severity, category, confidence, expected) in zip(ids, CASES):
        assert got[did] == expected == priority_of(severity, category, confidence), (severity, category, confidence)


async def test_the_model_is_not_vacuous():
    points = _model(30)
    assert any(p["opened"] and p["closed"] for p in points), "a same-day open and close"
    assert len({p["open_backlog"] for p in points}) > 2
    assert date.fromisoformat(points[-1]["date"]) == TODAY
