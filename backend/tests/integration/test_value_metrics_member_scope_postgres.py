"""W35 STRAT-01 -- value metrics over a member's OWN projects, on real Postgres + Redis.

World: active projects A, B and C, and a soft-deleted D. A QA engineer is a
member of A, B and D -- never C; an admin. Every counted row is seeded per
project in a distinct power of two (A x1, B x2, C x4, D x8), so a sum names the
projects it came from: 3 is A + B, 7 is A + B + C, and a foreign or deleted
row shows up as an extra 4 or 8.

1. The member's unpinned ``GET /value-metrics`` (read cold, before anyone else)
   counts A + B: C (foreign) and D (soft-deleted, though still a membership)
   never count, and ``meta`` lists A and B only.
2. Right after the admin's all-projects read, with real Redis, the member still
   reads A + B, and no unpinned hours-model entry exists to share.
3. The admin's all-projects read counts A + B + C (a delta over the rows the
   database already held).
4. The member's unpinned export equals the GET; a foreign project's export is a
   403 and its GET the empty payload; the member's pinned A reads A alone.
5. The hours-model leg queries and the run span, given the member's set, count
   A + B only; an empty set counts nothing.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (a throwaway
database). Everything seeded is deleted at the end.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

NOW = datetime.now(timezone.utc)
PATH = "/api/v1/value-metrics"
WEIGHTS = {"a": 1, "b": 2, "c": 4, "d": 8}
#: Days ago each project's runs happened (the run span tells them apart).
AT_DAYS = {"a": 2, "b": 3, "c": 10, "d": 12}
#: Each legacy counter per unit of weight (0: nothing seeded feeds it).
PER_UNIT = {
    "defects_auto_grouped": 1, "tests_grouped": 2, "duplicate_tickets_avoided": 1,
    "defects_promoted": 1, "flaky_tests_identified": 1, "quarantine_recommended": 1,
    "risky_releases_blocked": 1, "releases_conditional": 0, "release_overrides": 0,
    "intelligence_reports_generated": 0,
}
#: The hours-model legs per unit of weight.
LEGS_PER_UNIT = {
    "clustered_failures": 2, "duplicates_absorbed": 1, "auto_triaged": 1,
    "quarantine_suppressed_failures": 1, "runs_unblocked_proxy": 1,
}


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


def _ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def _counters(body: dict) -> dict:
    return {key: body[key] for key in PER_UNIT}


def _expected(weight: int) -> dict:
    return {key: per * weight for key, per in PER_UNIT.items()}


async def _seed(db, m, pid, weight: int, at: datetime, tag: str) -> None:
    """One project's rows, each count = its per-unit figure x ``weight``."""
    stem = f"vm-{tag}-{weight}"
    run_fail, q_runs = uuid.uuid4(), [uuid.uuid4() for _ in range(weight)]
    common = {"project_id": pid, "jenkins_job": "vm", "status": m.LaunchStatus.FAILED,
              "ingestion_source": "sdk", "passed_tests": 0, "skipped_tests": 0}
    db.add(m.TestRun(id=run_fail, build_number=f"{stem}-f", total_tests=2 * weight,
                     failed_tests=2 * weight, created_at=at, **common))
    for i, run_q in enumerate(q_runs):
        db.add(m.TestRun(id=run_q, build_number=f"{stem}-q{i}", total_tests=1, failed_tests=1,
                         created_at=at + timedelta(minutes=i + 1), **common))
    await db.flush()
    cases = [uuid.uuid4() for _ in range(2 * weight)]
    for i, case_id in enumerate(cases):
        db.add(m.TestCase(id=case_id, test_run_id=run_fail, test_fingerprint=f"{stem}-c{i}",
                          test_name=f"{stem} c{i}", suite_name="vm", status="FAILED", created_at=at))
    for i, run_q in enumerate(q_runs):
        # The run's only failure is quarantined: suppressed, and the run unblocked.
        db.add(m.TestCase(id=uuid.uuid4(), test_run_id=run_q, test_fingerprint=f"{stem}-flaky",
                          test_name=f"{stem} flaky", suite_name="vm", status="FAILED",
                          created_at=at + timedelta(minutes=i + 1)))
        db.add(m.ReleaseDecision(test_run_id=run_q, recommendation="NO_GO", risk_score=80))
    await db.flush()
    for i in range(weight):
        db.add(m.FailureCluster(test_run_id=run_fail, cluster_id=f"cl_{i:03d}", label=f"{stem} {i}",
                                size=2, member_test_ids=[str(cases[2 * i]), str(cases[2 * i + 1])],
                                created_at=at))
        db.add(m.AIAnalysis(test_case_id=cases[i]))
        db.add(m.Defect(project_id=pid, title=f"{stem} duplicate {i}", is_duplicate=True))
        db.add(m.Defect(project_id=pid, title=f"{stem} promoted {i}", promotion_source="cluster_promotion"))
        db.add(m.FlakyCoachResult(project_id=pid, test_fingerprint=f"{stem}-coach{i}", test_name=f"{stem} coach",
                                  failure_rate=0.5, total_runs=6, failed_runs=3,
                                  quarantine_recommendation="QUARANTINE",
                                  status_history=["PASSED", "FAILED"] * 3))
    db.add(m.FlakyQuarantineRequest(project_id=pid, test_fingerprint=f"{stem}-flaky", test_name=f"{stem} flaky",
                                    status="QUARANTINED", quarantine_start=at - timedelta(days=1)))


def _redis_client():
    import redis.asyncio as redis_asyncio

    return redis_asyncio.Redis.from_url(_env("REDIS_URL"), decode_responses=True)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from httpx import ASGITransport, AsyncClient
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.core import analytics_errors as errors
    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.models import postgres as models
    from app.routers import value_metrics
    from app.services import value_metrics_service as vms

    dsn = _env("TESTLOOKUP_POSTGRES_TEST_DSN")
    redis = _redis_client()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, errors.http_exception_handler)
    app.add_exception_handler(RequestValidationError, errors.validation_exception_handler)
    app.add_exception_handler(errors.AnalyticsQueryError, errors.analytics_query_error_handler)
    app.include_router(value_metrics.router)
    app.dependency_overrides[get_db] = _get_db
    patch = pytest.MonkeyPatch()
    # Real Redis (the wrapper's db), bound to this module's loop: the membership
    # cache and the hours-model cache both go through it.
    patch.setattr("app.db.redis_client.get_redis", lambda: redis)
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    tag = uuid.uuid4().hex[:10]
    ids = {name: uuid.uuid4() for name in WEIGHTS}
    member, admin = uuid.uuid4(), uuid.uuid4()
    try:
        async with sessions() as db:
            for name, pid in ids.items():
                db.add(models.Project(id=pid, name=f"vm-{name}-{tag}", slug=f"vm-{name}-{tag}",
                                      is_active=name != "d"))
            for uid, label, role in ((member, "member", models.UserRole.QA_ENGINEER),
                                     (admin, "admin", models.UserRole.ADMIN)):
                db.add(models.User(id=uid, email=f"vm-{label}-{tag}@example.com", username=f"vm_{label}_{tag}",
                                   full_name=f"VM {label}", hashed_password="!unusable", role=role.value))
            await db.flush()
            for name in ("a", "b", "d"):
                db.add(models.ProjectMember(project_id=ids[name], user_id=member,
                                            role=models.UserRole.QA_ENGINEER.value))
            await db.commit()
        # What every live project in this database already counts, before the seed.
        async with sessions() as db:
            baseline = _counters(await vms.get_value_metrics(db, None, days=30))
        async with sessions() as db:
            for name, pid in ids.items():
                await _seed(db, models, pid, WEIGHTS[name], _ago(AT_DAYS[name]), tag)
            await db.commit()
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        headers = {
            who: {"Authorization": f"Bearer {create_access_token(str(uid))}"}
            for who, uid in (("member", member), ("admin", admin))
        }
        # 1. The member's read on a cold cache, before any admin read.
        await redis.delete(f"membership:{member}", f"membership:{admin}")
        cold = await client.get(PATH, params={"days": "30"}, headers=headers["member"])
        try:
            yield SimpleNamespace(
                client=client, sessions=sessions, redis=redis, ids=ids, tag=tag, headers=headers,
                baseline=baseline, cold=cold, member_set=frozenset(ids[n] for n in ("a", "b", "d")),
            )
        finally:
            await client.aclose()
    finally:
        for statement in (
            "DELETE FROM defects WHERE project_id = ANY(:ids)",
            "DELETE FROM flaky_coach_results WHERE project_id = ANY(:ids)",
            "DELETE FROM flaky_quarantine_requests WHERE project_id = ANY(:ids)",
            # Cascades: runs -> cases -> ai_analysis; clusters, release decisions, memberships.
            "DELETE FROM projects WHERE id = ANY(:ids)",
            "DELETE FROM access_audit_logs WHERE actor_user_id = ANY(:users)",
            "DELETE FROM users WHERE id = ANY(:users)",
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(text(statement), {"ids": list(ids.values()), "users": [member, admin]})
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"value-metrics member teardown: {type(exc).__name__}: {str(exc)[:160]}")
        stale = [f"membership:{member}", f"membership:{admin}"]
        for pid in ids.values():
            stale += [key async for key in redis.scan_iter(match=f"analytics:value_metrics_hours:{pid}:*")]
        await redis.delete(*stale)
        await redis.aclose()
        patch.undo()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


def _assert_member_aggregate(world, body: dict) -> None:
    """A + B (weight 3): C (foreign, 4) and D (deleted, 8) never count."""
    assert _counters(body) == _expected(WEIGHTS["a"] + WEIGHTS["b"])
    assert body["project_id"] is None
    # The default rate card (triage 20, filing 15 min) over A + B's counts.
    assert body["triage_time_saved_minutes"] == 3 * 20 + 3 * 15
    # Unpinned, the hours-saved model is the "select a project" one -- as for an admin.
    assert body["available"] is False
    assert body["insufficient_data_reason"] == "select a project to compute the hours-saved model"
    assert body["assumptions_source"] == "default"
    projects = {p["id"] for p in body["meta"]["scope"]["projects"]}
    assert projects == {str(world.ids["a"]), str(world.ids["b"])}
    assert body["meta"]["measured"] is True


# ── 1. cold ─────────────────────────────────────────────────────────────────


async def test_the_members_unpinned_read_counts_only_their_live_projects(world):
    assert world.cold.status_code == 200, world.cold.text[:300]
    _assert_member_aggregate(world, world.cold.json())


# ── 2 + 3. after the admin warmed every cache ──────────────────────────────


async def test_after_the_admins_all_projects_read_the_member_still_reads_a_plus_b(world):
    admin = await world.client.get(PATH, params={"days": "30"}, headers=world.headers["admin"])
    assert admin.status_code == 200, admin.text[:300]
    admin_body = admin.json()
    # 3. The admin counts A + B + C over what the database already held; D is deleted.
    weight = WEIGHTS["a"] + WEIGHTS["b"] + WEIGHTS["c"]
    assert _counters(admin_body) == {
        key: world.baseline[key] + per * weight for key, per in PER_UNIT.items()
    }
    assert admin_body["available"] is False  # unpinned: the "select a project" model

    member = await world.client.get(PATH, params={"days": "30"}, headers=world.headers["member"])
    assert member.status_code == 200, member.text[:300]
    _assert_member_aggregate(world, member.json())
    # Nothing unpinned was cached for anyone to share: such a key would carry
    # "None" (the model keys on ``str(project_id)``) or "all" (the builder's default).
    for segment in ("None", "all"):
        pattern = f"analytics:value_metrics_hours:{segment}:*"
        assert [key async for key in world.redis.scan_iter(match=pattern)] == [], pattern


# ── 4. the export, the foreign project, the pinned read ────────────────────


async def test_the_members_unpinned_export_is_the_same_aggregate(world):
    resp = await world.client.get(f"{PATH}/export", params={"days": "30"}, headers=world.headers["member"])
    assert resp.status_code == 200, resp.text[:300]
    body = json.loads(resp.content)
    assert body["report_type"] == "value_metrics"
    assert _counters(body) == _expected(WEIGHTS["a"] + WEIGHTS["b"])
    assert body["project_id"] is None


async def test_a_foreign_project_is_a_403_export_and_an_empty_read(world):
    foreign = {"project_id": str(world.ids["c"]), "days": "30"}
    export = await world.client.get(f"{PATH}/export", params=foreign, headers=world.headers["member"])
    assert export.status_code == 403
    read = await world.client.get(PATH, params=foreign, headers=world.headers["member"])
    assert read.status_code == 200
    body = read.json()
    assert set(body) == {"meta"} and body["meta"]["measured"] is False


async def test_the_members_pinned_project_reads_that_project_alone(world):
    pinned = {"project_id": str(world.ids["a"]), "days": "30"}
    resp = await world.client.get(PATH, params=pinned, headers=world.headers["member"])
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()
    assert _counters(body) == _expected(WEIGHTS["a"])
    assert body["project_id"] == str(world.ids["a"])


# ── 5. the hours-model queries take the set ────────────────────────────────


async def test_the_leg_queries_and_the_run_span_read_only_the_set(world):
    from app.services import value_metrics_service as vms

    since = _ago(30)
    async with world.sessions() as db:
        legs = await vms._gather_leg_counts(db, None, since, monthly=False,
                                            allowed_project_ids=world.member_set)
        nothing = await vms._gather_leg_counts(db, None, since, monthly=False,
                                               allowed_project_ids=frozenset())
        pinned = await vms._gather_leg_counts(db, world.ids["a"], since, monthly=False)
        first, last = await vms._run_span(db, None, allowed_project_ids=world.member_set)
        empty_span = await vms._run_span(db, None, allowed_project_ids=frozenset())
    weight = WEIGHTS["a"] + WEIGHTS["b"]
    assert legs[vms._TOTAL_KEY] == {key: per * weight for key, per in LEGS_PER_UNIT.items()}
    assert all(not any(counts.values()) for counts in nothing.values())
    assert pinned[vms._TOTAL_KEY] == {key: per * WEIGHTS["a"] for key, per in LEGS_PER_UNIT.items()}
    # B's runs are the oldest of the set (3 days); C's (10) and D's (12) are outside it.
    assert _ago(AT_DAYS["b"] + 0.5) <= first <= last <= _ago(AT_DAYS["a"] - 0.5)
    assert empty_span == (None, None)
