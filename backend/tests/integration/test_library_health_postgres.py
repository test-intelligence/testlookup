"""W13 TCL-03 against real PostgreSQL: library health over the WHOLE library.

3. 250 authored cases -> ``total_cases == 250`` (the old 200 roll fails it);
   "retired in open plans" measures plans: a deprecated case in an open plan
   counts, one carrying only a suite name does not, one in a completed plan
   does not, an archived one in an in-progress plan does.
4. ``flaky_active`` needs the automation identity (own fingerprint OR the
   promote link) AND a score >= 0.5 AND a confidence above low; project B's
   score row with the SAME fingerprint never surfaces in A; no score row ->
   not measured, with the sentence.
5. ``no_owner``: unassigned + deactivated author counted, an active assignee
   not; the review queue is oldest first by ``lifecycle_state_changed_at``;
   ``suite_names`` distinct, trimmed, sorted.
6. A GET writes nothing. Every time is built inside the test from one ``now``.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, event, func, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.test_library_vocab import FLAKY_UNMEASURED_NO_SCORES
from app.models.postgres import (
    CanonicalTestCase,
    FlakyScore,
    ManagedTestCase,
    Project,
    TestPlan as Plan,
    TestPlanItem as PlanItem,
    TestSuite as Suite,
    User,
    UserRole,
)
from app.routers import test_management_library as routes
from app.services import feature_flags
from app.services.test_library_health_service import library_health
from app.services.test_suite_service import list_test_case_evidence_gaps

asyncpg = pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

FILLERS = 239          # + 6 flaky + 3 owner + 2 review cases = 250 non-retired


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def pg_engine():
    # The app's own engine is lazily built on whichever loop first touches it
    # (a route's activity write, a service's own session); dispose it before
    # and after, or its pooled connections outlive this test's loop and the
    # next module that uses AsyncSessionLocal gets "Event loop is closed".
    from app.db import postgres as app_postgres

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    try:
        yield engine
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


def _user(token: str, name: str, *, active: bool = True, role: UserRole = UserRole.TESTER) -> User:
    return User(
        id=uuid.uuid4(), email=f"w13h-{name}-{token}@example.invalid", username=f"w13h-{name}-{token}",
        full_name=f"W13 {name.title()}", hashed_password="not-a-real-password", role=role, is_active=active,
    )


def _case(project_id, title, now, *, status="active", author=None, assignee=None, **extra) -> dict:
    """Every row carries the SAME keys (one executemany batch)."""
    row = dict(
        id=uuid.uuid4(), project_id=project_id, title=title, status=status, version=1, priority="medium",
        severity="major", test_type="functional", is_automated=False, automation_status="not_automated",
        ai_generated=False, author_id=author, assignee_id=assignee, suite_name=None, test_fingerprint=None,
        created_at=now - timedelta(days=5), updated_at=now - timedelta(days=2), lifecycle_state_changed_at=None,
    )
    row.update(extra)
    return row


@pytest.fixture
async def world(pg_engine):
    now = datetime.now(timezone.utc)
    token = uuid.uuid4().hex[:10]
    alice, dora = _user(token, "alice"), _user(token, "dora", active=False)
    admin = _user(token, "admin", role=UserRole.ADMIN)
    a = Project(id=uuid.uuid4(), name=f"W13 health A {token}", slug=f"w13-health-a-{token}")
    b = Project(id=uuid.uuid4(), name=f"W13 health B {token}", slug=f"w13-health-b-{token}")
    fp = {k: f"w13h-{k}-{token}" for k in ("own", "promo", "low", "weak", "draft", "bonly")}
    suites = [" Checkout ", "Checkout", "API", "", None]
    rows = [
        _case(a.id, f"Filler {i}", now, author=alice.id, suite_name=suites[i % 5], is_automated=i % 3 == 0,
              created_at=now - timedelta(days=200 if i < 10 else 20))
        for i in range(FILLERS)
    ]
    ids = {}

    def add(key, project_id, title, **kw):
        row = _case(project_id, title, now, **kw)
        ids[key] = row["id"]
        rows.append(row)

    add("f1", a.id, "Flaky own", author=alice.id, test_fingerprint=fp["own"])
    add("f2", a.id, "Flaky promoted", status="approved", author=alice.id)
    add("f3", a.id, "Low confidence", author=alice.id, test_fingerprint=fp["low"])
    add("f4", a.id, "Below the floor", author=alice.id, test_fingerprint=fp["weak"])
    add("f5", a.id, "A flaky draft", status="draft", author=alice.id, test_fingerprint=fp["draft"])
    add("f6", a.id, "Scored in B only", author=alice.id, test_fingerprint=fp["bonly"])
    add("n1", a.id, "Nobody's", status="draft", created_at=now - timedelta(days=50),
        updated_at=now - timedelta(days=45))
    add("n2", a.id, "Deactivated author's", author=dora.id, created_at=now - timedelta(days=40))
    add("n3", a.id, "Assigned to an active tester", author=dora.id, assignee=alice.id)
    add("r1", a.id, "Review ten days", status="review_requested", author=alice.id,
        lifecycle_state_changed_at=now - timedelta(days=10), updated_at=now - timedelta(days=1))
    add("r2", a.id, "Review three days", status="under_review", author=alice.id,
        lifecycle_state_changed_at=now - timedelta(days=3), updated_at=now - timedelta(days=20))
    for key, status in (("dep_open", "deprecated"), ("dep_suite", "deprecated"), ("dep_done", "deprecated"),
                        ("arch_open", "archived")):
        add(key, a.id, f"Retired {key}", status=status, author=alice.id, suite_name="Legacy")
    add("b_orphan", b.id, "B nobody's")
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all([alice, dora, admin, a, b])
        await s.flush()
        await s.execute(insert(ManagedTestCase), rows)
        suite = Suite(id=uuid.uuid4(), project_id=a.id, name=f"W13 health suite {token}")
        s.add(suite)
        await s.flush()
        s.add(CanonicalTestCase(project_id=a.id, test_suite_id=suite.id, test_fingerprint=fp["promo"],
                                test_name="promoted", managed_test_case_id=ids["f2"]))
        for project_id, key, score, confidence in (
            (a.id, "own", 0.8, "high"), (a.id, "promo", 0.6, "medium"), (a.id, "low", 0.9, "low"),
            (a.id, "weak", 0.4, "high"), (a.id, "draft", 0.9, "high"), (b.id, "bonly", 0.95, "high"),
        ):
            s.add(FlakyScore(project_id=project_id, test_fingerprint=fp[key], score=score, confidence=confidence))
        plans = {status: Plan(id=uuid.uuid4(), project_id=a.id, name=f"Plan {status}", status=status)
                 for status in ("in_progress", "completed")}
        s.add_all(plans.values())
        await s.flush()
        for case_key, plan_status in (("dep_open", "in_progress"), ("dep_done", "completed"),
                                      ("arch_open", "in_progress"), ("f1", "in_progress")):
            s.add(PlanItem(plan_id=plans[plan_status].id, test_case_id=ids[case_key]))
        await s.commit()
    try:
        yield SimpleNamespace(factory=factory, now=now, a=a, b=b, ids=ids, admin=admin, engine=pg_engine)
    finally:
        async with factory() as s:
            await s.execute(delete(CanonicalTestCase).where(CanonicalTestCase.project_id.in_([a.id, b.id])))
            await s.execute(delete(Project).where(Project.id.in_([a.id, b.id])))
            await s.execute(delete(User).where(User.id.in_([alice.id, dora.id, admin.id])))
            await s.commit()


async def _counts(session, project_id) -> tuple:
    out = []
    for model, column in ((ManagedTestCase, ManagedTestCase.project_id), (FlakyScore, FlakyScore.project_id),
                          (Project, Project.id), (Plan, Plan.project_id)):
        out.append((await session.execute(select(func.count()).select_from(model).where(column == project_id)))
                   .scalar())
    out.append((await session.execute(select(func.count()).select_from(PlanItem))).scalar())
    return tuple(out)


@pytest.fixture
def statements(world):
    seen: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(world.engine.sync_engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(world.engine.sync_engine, "before_cursor_execute", _record)


async def test_the_whole_library_is_measured_and_a_get_writes_nothing(world, statements, monkeypatch):
    async def _off(key, **kwargs):
        return False

    monkeypatch.setattr(feature_flags, "is_enabled", _off)
    async with world.factory() as s:
        before = await _counts(s, world.a.id)
        statements.clear()
        body = await routes.get_library_health(project_id=world.a.id, db=s, current_user=world.admin)
        health_statements = list(statements)
        assert not s.new and not s.dirty and not s.deleted
        after = await _counts(s, world.a.id)
    assert before == after
    assert not [q for q in health_statements if q.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}]
    assert len(health_statements) == 8, health_statements      # the flag off: no requirement read

    assert body["total_cases"] == 250                          # the 200-row roll could never say this
    assert body["retired_cases"] == 4 and body["active_cases"] == 245 and body["automated_cases"] == 80
    assert body["retired_in_open_plans"] == 2                  # dep_open + arch_open, not dep_suite / dep_done
    assert body["stale_drafts"] == 1 and body["older_than_180_days"] == 10
    assert body["older_than_180_pct"] == 4.0
    assert body["automation_target_pct"] == 60
    assert body["review_queue"]["total"] == 2
    assert [i["id"] for i in body["review_queue"]["items"]] == [world.ids["r1"], world.ids["r2"]]
    assert body["review_queue"]["oldest_age_days"] == 10
    assert body["flaky_active"]["measured"] is True and body["flaky_active"]["total"] == 2
    assert [i["id"] for i in body["flaky_active"]["items"]] == [world.ids["f1"], world.ids["f2"]]
    assert body["no_owner"]["total"] == 2
    assert [(i["id"], i["detail"]) for i in body["no_owner"]["items"]] == [
        (world.ids["n1"], "unassigned"), (world.ids["n2"], "owner deactivated"),
    ]
    assert body["suite_names"] == ["API", "Checkout"] and body["suite_names_total"] == 2
    assert body["requirements"] is None and body["score"] is not None


async def test_the_evidence_gap_pages_agree_and_stay_in_their_project(world):
    async with world.factory() as s:
        flaky, flaky_total = await list_test_case_evidence_gaps(s, [world.a.id], kind="flaky_active")
        owners, owners_total = await list_test_case_evidence_gaps(s, [world.a.id], kind="no_owner", size=1)
        b_flaky, b_flaky_total = await list_test_case_evidence_gaps(s, [world.b.id], kind="flaky_active")
        b_owners, _ = await list_test_case_evidence_gaps(s, [world.b.id], kind="no_owner")
    assert [i["id"] for i in flaky] == [world.ids["f1"], world.ids["f2"]] and flaky_total == 2
    assert [i["flaky_score"] for i in flaky] == [0.8, 0.6]
    assert [i["id"] for i in owners] == [world.ids["n1"]] and owners_total == 2   # a page of 1, the total 2
    assert owners[0]["owner_state"] == "unassigned"
    assert (b_flaky, b_flaky_total) == ([], 0)                 # B's score row has no B case behind it
    assert [i["id"] for i in b_owners] == [world.ids["b_orphan"]]


async def test_no_score_row_is_not_measured_with_its_sentence(world):
    async with world.factory() as s:
        await s.execute(delete(FlakyScore).where(FlakyScore.project_id == world.a.id))
        await s.commit()
        body = await library_health(s, project_id=world.a.id, now=world.now, include_requirements=False)
    assert body["flaky_active"] == {"total": 0, "items": [], "measured": False,
                                    "reason": FLAKY_UNMEASURED_NO_SCORES}
    assert "flaky_active" not in [g["kind"] for g in body["strategy_gaps"]]
