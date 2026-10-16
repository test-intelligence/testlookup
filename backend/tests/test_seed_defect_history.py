"""The defect-history demo seed (W06 DEF-04 / DEF-03, contract §2.4 / §7.3 step 0).

Pure plan: deterministic per slug, explicit times (no close in the future, every
close with a ``resolved_at``), 12 history rows, and -- only when a release has
shipped -- 7 rows filed after it and 2 before it, naming it. Apply: deletes ONLY
the marker rows of that project, then adds the plan.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from scripts import seed_defect_history as seed

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def _release(days_ago, name="v2.4.0"):
    return SimpleNamespace(id=uuid.uuid4(), name=name,
                           released_at=None if days_ago is None else NOW - timedelta(days=days_ago))


def test_the_plan_is_deterministic_per_slug():
    releases = [_release(10)]
    assert seed.build_defect_history_plan("payments", NOW, releases) == seed.build_defect_history_plan(
        "payments", NOW, releases)
    assert seed.build_defect_history_plan("payments", NOW, []) != seed.build_defect_history_plan("search", NOW, [])


def test_the_history_rows():
    rows = seed.build_defect_history_plan("payments", NOW, [])
    assert len(rows) == 12
    statuses = sorted(r["resolution_status"] for r in rows)
    assert statuses.count("RESOLVED") == 8 and statuses.count("INVALID") == 2 and statuses.count("OPEN") == 2
    assert {r["severity"] for r in rows} == {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
    for r in rows:
        assert NOW - timedelta(days=30) < r["created_at"] < NOW
        assert r["promotion_source"] == "seed_defect_history"
        assert r["affects_releases"] is None
        assert "project_id" not in r and "id" not in r
        if r["resolution_status"] == "OPEN":
            assert r["resolved_at"] is None
        else:
            assert r["created_at"] + timedelta(days=1) <= r["resolved_at"] <= r["created_at"] + timedelta(days=6)
            assert r["resolved_at"] < NOW, "a seed close never lands in the future"


def test_a_shipped_release_adds_seven_escapes_and_two_pre_ship_rows():
    release = _release(10)
    rows = seed.build_defect_history_plan("payments", NOW, [_release(None, "next"), release, _release(40, "old")])
    attributed = [r for r in rows if r["affects_releases"]]
    assert len(rows) == 21 and len(attributed) == 9
    assert {tuple(r["affects_releases"]) for r in attributed} == {(str(release.id).lower(),)}
    after = [r for r in attributed if r["created_at"] > release.released_at]
    assert len(after) == 7 and all(r["created_at"] < NOW for r in after)
    assert seed.expected_escape_rate(rows, NOW, release.released_at) == (7, 9)  # 77.8 %


def test_an_old_release_keeps_the_escapes_in_the_window_and_says_what_the_tile_reads():
    release = _release(38)
    rows = seed.build_defect_history_plan("payments", NOW, [release])
    after = [r for r in rows if r["affects_releases"] and r["created_at"] > release.released_at]
    assert all(r["created_at"] >= NOW - timedelta(days=30) for r in after)
    assert seed.expected_escape_rate(rows, NOW, release.released_at) == (7, 7)
    assert seed.expected_escape_rate(rows, NOW, release.released_at, days=90) == (7, 9)


def test_a_release_shipped_minutes_ago_still_gets_post_ship_rows():
    release = SimpleNamespace(id=uuid.uuid4(), name="now", released_at=NOW - timedelta(minutes=10))
    rows = seed.build_defect_history_plan("payments", NOW, [release])
    after = [r for r in rows if r["affects_releases"] and r["created_at"] > release.released_at]
    assert len(after) == 7 and all(r["created_at"] < NOW for r in after)


def test_a_future_or_missing_release_date_plans_no_escape_rows():
    rows = seed.build_defect_history_plan("payments", NOW, [_release(None), _release(-3)])
    assert len(rows) == 12


class _Session:
    def __init__(self, releases):
        self.releases = releases
        self.statements: list = []
        self.added: list = []
        self.flush = AsyncMock()

    def add(self, obj):
        self.added.append(obj)

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        if stmt.is_delete:
            return SimpleNamespace(rowcount=3)
        return SimpleNamespace(all=lambda: self.releases)


@pytest.mark.asyncio
async def test_apply_deletes_only_this_projects_marker_rows_then_adds_the_plan():
    project = SimpleNamespace(id=uuid.uuid4(), slug="payments")
    release = _release(10)
    session = _Session([release])

    summary = await seed.apply_defect_history(session, project, now=NOW)

    delete_stmt = session.statements[0]
    sql = str(delete_stmt.compile(dialect=postgresql.dialect()))
    params = delete_stmt.compile(dialect=postgresql.dialect()).params
    assert sql.startswith("DELETE FROM defects WHERE defects.project_id =")
    assert "defects.promotion_source =" in sql
    assert set(params.values()) == {project.id, "seed_defect_history"}
    assert len(session.added) == 21
    assert all(d.project_id == project.id and d.promotion_source == "seed_defect_history" for d in session.added)
    assert summary == {"deleted": 3, "history": 12, "attributed": 9, "release": "v2.4.0", "expected_30d": (7, 9)}
    text = seed.describe(summary, "payments")
    assert "9 defects affecting v2.4.0 (7 after it shipped, 2 before)" in text
    assert "7 of 9 = 77.8%" in text


@pytest.mark.asyncio
async def test_no_shipped_release_is_said_plainly():
    summary = await seed.apply_defect_history(_Session([]), SimpleNamespace(id=uuid.uuid4(), slug="s"), now=NOW)
    assert summary["attributed"] == 0 and summary["release"] is None
    assert "no shipped release: escape-rate rows skipped" in seed.describe(summary, "s")
