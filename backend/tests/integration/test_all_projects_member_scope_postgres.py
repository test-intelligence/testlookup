"""STRAT-01 / D28 on real PostgreSQL + Redis: All Projects for a non-admin counts ONLY their projects.

Seeds live projects A, B (the member's) and C (foreign), a QA engineer who is a
member of A and B, one run in each with known pass / fail / broken counts, the
failing per-test rows behind them, an open HIGH defect in A and an open
CRITICAL defect in C. Through the real app, with no ``project_id``:

* ``/metrics/summary``, ``/metrics/trends`` and ``/analytics/trend-summary``
  for the member equal the A + B sums exactly; C never counts;
* cache isolation: the admin's All Projects reads go FIRST (they warm the
  shared ``all`` entries and count A + B + C), and the member's reads that
  follow still return A + B, never the admin's entry;
* the member naming C, and a user with no project at all, get the empty body.

Uses ``tests/integration/defect_w06_world.py`` (projects, users, the ASGI
client; everything it creates is deleted afterwards, cascading to runs, cases,
defects and memberships). The Redis keys this test causes are deleted too; the
shared ``analytics_epoch:__all__`` counter is only ever bumped, never deleted.
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head and ``REDIS_URL``.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.integration.defect_w06_world import defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

#: (passed, failed, broken) per project; each run's total is their sum.
COUNTS = {"A": (8, 2, 0), "B": (5, 0, 1), "C": (2, 5, 0)}
MEMBER_TOTAL = sum(sum(COUNTS[k]) for k in "AB")                       # 16
MEMBER_PASSED = COUNTS["A"][0] + COUNTS["B"][0]                         # 13
MEMBER_FAILING = sum(COUNTS[k][1] + COUNTS[k][2] for k in "AB")         # 3
FOREIGN_TOTAL = sum(COUNTS["C"])                                        # 7


async def _redis_cleanup(keys_like: tuple[str, ...], exact: tuple[str, ...]) -> None:
    import redis.asyncio as redis_asyncio

    client = redis_asyncio.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    try:
        doomed = list(exact)
        for pattern in keys_like:
            doomed.extend([key async for key in client.scan_iter(match=pattern)])
        if doomed:
            await client.delete(*doomed)
    finally:
        await client.aclose()


@pytest.fixture
async def world():
    if not os.environ.get("REDIS_URL", "").strip():
        pytest.skip("REDIS_URL is not configured (the cache-isolation half needs the real Redis)")
    from app.models.postgres import LaunchStatus, ProjectMember, TestRun, UserRole
    from app.services import cache_service

    async with defect_world() as w:
        now = datetime.now(timezone.utc) - timedelta(minutes=2)
        async with w.sessions.begin() as db:
            ids = {key: await w.project(db) for key in "ABC"}
            w.A, w.B, w.C = ids["A"], ids["B"], ids["C"]
            w.mine_id = await w.user(db)
            w.lonely_id = await w.user(db)  # a non-admin with no project at all
            for pid in (w.A, w.B):
                db.add(ProjectMember(project_id=pid, user_id=w.mine_id, role=UserRole.QA_ENGINEER.value))
            for key, pid in ids.items():
                passed, failed, broken = COUNTS[key]
                run_id = uuid.uuid4()
                db.add(TestRun(
                    id=run_id, project_id=pid, build_number=f"strat01-{key}-{w.tag}", jenkins_job="strat01",
                    status=LaunchStatus.FAILED if failed or broken else LaunchStatus.PASSED,
                    ingestion_source="unknown", total_tests=passed + failed + broken, passed_tests=passed,
                    failed_tests=failed, skipped_tests=0, broken_tests=broken, created_at=now, start_time=now,
                ))
                await db.flush()
                for status, n in (("FAILED", failed), ("BROKEN", broken)):
                    for _ in range(n):
                        await w.case(db, run_id=run_id, status=status, suite_name="strat01", created_at=now)
            await w.defect(db, project_id=w.A, severity="HIGH")
            await w.defect(db, project_id=w.C, severity="CRITICAL")
        w.mine = w.jwt(w.mine_id)
        w.lonely = w.jwt(w.lonely_id)
        # A fresh all-projects epoch: no entry cached before this seed can be
        # served (the admin's read below must see C).
        await cache_service.bump_analytics_epoch(w.C)
        try:
            yield w
        finally:
            await _redis_cleanup(
                ("analytics:dashboard_summary_v2:all:*", "analytics:trend_summary:*"),
                tuple(f"membership:{uid}" for uid in (w.mine_id, w.lonely_id, w.admin_id))
                + tuple(f"analytics_epoch:{pid}" for pid in ids.values()),
            )


async def _get(w, path: str, headers: dict, **params) -> dict:
    response = await w.client.get(path, params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _trend_sums(body: dict) -> SimpleNamespace:
    rows = body["data"]
    return SimpleNamespace(**{k: sum(r[k] for r in rows) for k in ("total", "passed", "failed", "broken")})


def _assert_member_summary(body: dict, w) -> None:
    assert body["total_executions_7d"]["value"] == MEMBER_TOTAL, "a foreign project's runs were counted"
    assert body["avg_pass_rate_7d"]["value"] == round(MEMBER_PASSED / MEMBER_TOTAL * 100, 1)
    assert body["new_failures_24h"]["value"] == MEMBER_FAILING
    assert body["active_defects"]["value"] == 1, "C's open CRITICAL defect was counted"
    assert body["flaky_test_count"]["value"] == 0
    meta = body["meta"]
    assert {p["id"] for p in meta["scope"]["projects"]} == {str(w.A), str(w.B)}
    assert (meta["totals"]["total_runs"], meta["totals"]["total_executions"]) == (2, MEMBER_TOTAL)


async def test_the_member_reads_only_their_projects_even_after_the_admin_warmed_the_cache(world):
    w = world

    # 1. The admin's All Projects reads go first: they count C, and they warm
    #    the shared ``all`` entries (the summary's Redis key, the trend summary's
    #    read-layer entry).
    admin = await _get(w, "/api/v1/metrics/summary", w.admin, days=7)
    assert admin["total_executions_7d"]["value"] >= MEMBER_TOTAL + FOREIGN_TOTAL
    assert str(w.C) in {p["id"] for p in admin["meta"]["scope"]["projects"]}
    admin_summary = await _get(w, "/api/v1/analytics/trend-summary", w.admin, days=7)
    assert admin_summary["executions"]["total"] >= MEMBER_TOTAL + FOREIGN_TOTAL

    # 2. The member, same URLs: A + B exactly, never the admin's entries.
    member = await _get(w, "/api/v1/metrics/summary", w.mine, days=7)
    _assert_member_summary(member, w)
    trends = await _get(w, "/api/v1/metrics/trends", w.mine, days=7)
    sums = _trend_sums(trends)
    assert (sums.total, sums.passed, sums.failed, sums.broken) == (
        MEMBER_TOTAL, MEMBER_PASSED, COUNTS["A"][1], COUNTS["B"][2],
    )
    assert {p["id"] for p in trends["meta"]["scope"]["projects"]} == {str(w.A), str(w.B)}
    summary = await _get(w, "/api/v1/analytics/trend-summary", w.mine, days=7)
    assert summary["executions"]["total"] == MEMBER_TOTAL
    assert summary["executions"]["passed"] == MEMBER_PASSED
    assert {p["id"] for p in summary["meta"]["scope"]["projects"]} == {str(w.A), str(w.B)}

    # 3. Served again (now from the member's own entries): still A + B.
    _assert_member_summary(await _get(w, "/api/v1/metrics/summary", w.mine, days=7), w)
    again = await _get(w, "/api/v1/analytics/trend-summary", w.mine, days=7)
    assert again["executions"]["total"] == MEMBER_TOTAL


async def test_a_cold_member_read_counts_only_their_projects(world):
    w = world
    _assert_member_summary(await _get(w, "/api/v1/metrics/summary", w.mine, days=7), w)
    assert _trend_sums(await _get(w, "/api/v1/metrics/trends", w.mine, days=7)).total == MEMBER_TOTAL


async def test_the_readiness_p0_count_never_sees_the_foreign_critical(world):
    """The hard-cap input, directly: C's open CRITICAL is the admin's, not the member's."""
    from app.services.metrics_service import count_open_critical_defects

    w = world
    async with w.sessions() as db:
        member = await count_open_critical_defects(db, None, allowed_project_ids=frozenset({w.A, w.B}))
        admin = await count_open_critical_defects(db, None)
        pinned = await count_open_critical_defects(db, str(w.C))
    assert member == 0
    assert admin >= 1 and pinned == 1


async def test_a_foreign_project_and_a_caller_with_no_project_get_the_empty_body(world):
    w = world
    foreign = await _get(w, "/api/v1/metrics/summary", w.mine, project_id=str(w.C), days=7)
    assert set(foreign) == {"meta"} and foreign["meta"]["measured"] is False
    lonely = await _get(w, "/api/v1/metrics/summary", w.lonely, days=7)
    assert set(lonely) == {"meta"} and lonely["meta"]["scope"]["projects"] == []
    trends = await _get(w, "/api/v1/metrics/trends", w.lonely, days=7)
    assert trends["data"] == []
    summary = await _get(w, "/api/v1/analytics/trend-summary", w.lonely, days=7)
    assert summary["measured"] is False and summary["executions"]["total"] == 0
