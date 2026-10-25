"""W13 TCL-05 against real PostgreSQL: the case timeline.

6. A promoted, automated case of project A lists its runs newest first;
   project B holds a canonical with the SAME fingerprint, its runs, a defect and
   a ledger row naming the case's id: none of B's rows appear (MANDATORY:
   fingerprints are not project-salted).
7. 10,000 per-run results: the first page holds 50 and a cursor, the third
   page continues exactly, and the source statement carries LIMIT 51.
8. Two executions of one plan item -> two ``plan_execution`` entries, only the
   latest with its version; a review request + decision, a comment and a
   requirement link event, each with its actor; through the real route, another
   project's case is 403 and the case's own answer validates.

Every time is built inside the test from one ``now``.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.deps import get_current_active_user, get_db
from app.models.postgres import (
    CanonicalTestCase,
    Defect,
    ManagedTestCase,
    Project,
    ProjectActivityEvent,
    ProjectMember,
    User,
    UserRole,
)
from app.models.postgres import TestCase as Result
from app.models.postgres import TestCaseAuditLog as AuditRow
from app.models.postgres import TestCaseComment as Comment
from app.models.postgres import TestCaseReview as Review
from app.models.postgres import TestCaseVersion as Version
from app.models.postgres import TestPlan as Plan
from app.models.postgres import TestPlanItem as PlanItem
from app.models.postgres import TestRun as Run
from app.models.postgres import TestSuite as Suite
from app.routers import test_management_library as routes
from app.services.test_case_timeline_service import case_timeline

asyncpg = pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def pg_engine():
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    try:
        yield engine
    finally:
        await engine.dispose()


def _user(token, name, role=UserRole.TESTER):
    return User(id=uuid.uuid4(), email=f"w13t-{name}-{token}@example.invalid", username=f"w13t-{name}-{token}",
                full_name=f"W13 {name.title()}", hashed_password="not-a-real-password", role=role, is_active=True)


def _managed(project_id, title, **kw):
    return ManagedTestCase(id=uuid.uuid4(), project_id=project_id, title=title, status="active", version=3,
                           priority="medium", severity="major", test_type="functional", is_automated=True,
                           automation_status="automated", ai_generated=False, **kw)


async def _runs(session, project_id, fingerprint, times, *, tag):
    """One run per time, each with one result of ``fingerprint``; returns [(run_id, result_id)]."""
    out = []
    for n, at in enumerate(times):
        run = Run(id=uuid.uuid4(), project_id=project_id, build_number=f"{tag}-{n}", branch="main", created_at=at)
        session.add(run)
        await session.flush()
        result = Result(id=uuid.uuid4(), test_run_id=run.id, test_fingerprint=fingerprint, test_name="checkout",
                        status="FAILED" if n % 2 else "PASSED", created_at=at)
        session.add(result)
        out.append((run.id, result.id))
    await session.flush()
    return out


@pytest.fixture
async def world(pg_engine):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    token = uuid.uuid4().hex[:10]
    fp = f"w13t-fp-{token}"
    alice, lead = _user(token, "alice"), _user(token, "lead", UserRole.QA_LEAD)
    a = Project(id=uuid.uuid4(), name=f"W13 timeline A {token}", slug=f"w13-timeline-a-{token}")
    b = Project(id=uuid.uuid4(), name=f"W13 timeline B {token}", slug=f"w13-timeline-b-{token}")
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all([alice, lead, a, b])
        await s.flush()
        s.add(ProjectMember(user_id=alice.id, project_id=a.id, role=UserRole.TESTER.value))
        case, b_case = _managed(a.id, "Checkout pays"), _managed(b.id, "B checkout")
        s.add_all([case, b_case])
        suites = {p.id: Suite(id=uuid.uuid4(), project_id=p.id, name=f"W13 tl {token}") for p in (a, b)}
        s.add_all(suites.values())
        await s.flush()
        s.add(CanonicalTestCase(project_id=a.id, test_suite_id=suites[a.id].id, test_fingerprint=fp,
                                test_name="checkout", managed_test_case_id=case.id))
        s.add(CanonicalTestCase(project_id=b.id, test_suite_id=suites[b.id].id, test_fingerprint=fp,
                                test_name="checkout"))
        a_runs = await _runs(s, a.id, fp, [now - timedelta(days=d) for d in (3, 1, 2)], tag=f"a-{token}")
        b_runs = await _runs(s, b.id, fp, [now - timedelta(hours=h) for h in (1, 2)], tag=f"b-{token}")
        s.add(Defect(project_id=a.id, test_case_id=a_runs[1][1], jira_ticket_id="PAY-123", title="Checkout broke",
                     resolution_status="OPEN", created_at=now - timedelta(hours=20)))
        s.add(Defect(project_id=b.id, test_case_id=b_runs[0][1], jira_ticket_id="B-9", title="B broke",
                     resolution_status="OPEN", created_at=now - timedelta(minutes=5)))
        plan = Plan(id=uuid.uuid4(), project_id=a.id, name="Sprint 42", status="in_progress")
        s.add(plan)
        await s.flush()
        first, latest = now - timedelta(days=4), now - timedelta(hours=30)
        item = PlanItem(plan_id=plan.id, test_case_id=case.id, execution_status="passed", executed_at=latest,
                        executed_case_version=3, executed_by_id=alice.id)
        s.add(item)
        await s.flush()
        for at, status in ((first, "failed"), (latest, "passed")):
            s.add(AuditRow(entity_type="test_plan_item", entity_id=item.id, project_id=a.id, action="executed",
                           actor_id=alice.id, actor_name="W13 Alice", created_at=at,
                           new_values={"execution_status": status, "executed_at": at.isoformat()}))
        s.add(Version(test_case_id=case.id, version=1, title="Checkout pays", status="draft", change_type="created",
                      source="person", changed_by_id=alice.id, created_at=now - timedelta(days=5)))
        s.add(Review(test_case_id=case.id, status="approved", requested_by_id=alice.id, reviewer_id=lead.id,
                     human_notes="Looks right", created_at=now - timedelta(days=4, hours=12),
                     reviewed_at=now - timedelta(days=4, hours=6)))
        s.add(Comment(test_case_id=case.id, author_id=lead.id, content="<script>x</script>", created_at=now))
        for project_id in (a.id, b.id):
            s.add(ProjectActivityEvent(
                project_id=project_id, category="test_management", event_type="test_case.requirement_linked",
                actor_type="user", actor_id=alice.id, actor_name="W13 Alice", entity_type="test_case",
                entity_id=str(case.id), entity_label="Checkout pays", summary="linked",
                context={"requirement_key": "REQ-PAY-07", "link_type": "verifies"},
                occurred_at=now - timedelta(minutes=30),
            ))
        await s.commit()
    try:
        yield SimpleNamespace(factory=factory, engine=pg_engine, now=now, a=a, b=b, case=case, b_case=b_case,
                              fp=fp, a_runs=a_runs, b_runs=b_runs, plan=plan, alice=alice, lead=lead, token=token)
    finally:
        async with factory() as s:
            await s.execute(delete(Defect).where(Defect.project_id.in_([a.id, b.id])))
            await s.execute(delete(CanonicalTestCase).where(CanonicalTestCase.project_id.in_([a.id, b.id])))
            await s.execute(delete(AuditRow).where(AuditRow.project_id.in_([a.id, b.id])))
            await s.execute(delete(Project).where(Project.id.in_([a.id, b.id])))
            await s.execute(delete(User).where(User.id.in_([alice.id, lead.id])))
            await s.commit()


async def _timeline(world, *, kinds=(), cursor=None, limit=50):
    async with world.factory() as s:
        case = await s.get(ManagedTestCase, world.case.id)
        return await case_timeline(s, case, kinds=list(kinds), cursor=cursor, limit=limit)


async def test_automated_runs_and_defects_stay_in_the_cases_project(world):
    body = await _timeline(world, kinds=["automated_execution", "defect", "requirement"])
    assert body["automation_linked"] is True
    runs = [e for e in body["items"] if e["kind"] == "automated_execution"]
    a_results = {result for _run, result in world.a_runs}
    assert [uuid.UUID(e["id"]) for e in runs] == [world.a_runs[1][1], world.a_runs[2][1], world.a_runs[0][1]]
    assert {uuid.UUID(e["id"]) for e in runs} == a_results          # newest first, and only A's
    assert all(e["actor_source"] == "automation" and e["link"]["type"] == "run_result" for e in runs)
    assert runs[0]["detail"].startswith("Build a-") and runs[0]["title"].startswith("Automated run · ")
    defects = [e for e in body["items"] if e["kind"] == "defect"]
    assert [(e["title"], e["detail"]) for e in defects] == [("Defect PAY-123 opened", "Checkout broke")]
    requirement = [e for e in body["items"] if e["kind"] == "requirement"]
    assert [e["title"] for e in requirement] == ["Linked REQ-PAY-07 (verifies)"]   # not B's ledger row
    b_ids = {str(result) for _run, result in world.b_runs}
    assert not b_ids & {e["id"] for e in body["items"]}


async def test_ten_thousand_results_page_by_fifty_and_continue_exactly(world):
    async with world.factory() as s:
        # 10,000 runs (one result of a fingerprint per run: uq_test_cases_run_fingerprint), two per
        # instant so page boundaries fall between equal timestamps.
        await s.execute(text(
            "INSERT INTO test_runs (id, project_id, build_number, status, created_at) "
            "SELECT gen_random_uuid(), :pid, 'bulk-' || g || '-' || :tok, 'PASSED', "
            "CAST(:base AS timestamptz) - make_interval(mins => g / 2) FROM generate_series(1, 10000) AS g"
        ), {"pid": world.a.id, "tok": world.token, "base": world.now - timedelta(days=10)})
        await s.execute(text(
            "INSERT INTO test_cases (id, test_run_id, test_fingerprint, test_name, status, created_at) "
            "SELECT gen_random_uuid(), tr.id, :fp, 'checkout', 'PASSED', tr.created_at FROM test_runs tr "
            "WHERE tr.project_id = :pid AND tr.build_number LIKE 'bulk-%'"
        ), {"fp": world.fp, "pid": world.a.id})
        await s.commit()
        # Statistics, as a live database has them: on a freshly migrated, empty one the planner
        # believes "1 row" and nested-loops 10,000 x 10,000 (seconds instead of a millisecond).
        await s.execute(text("ANALYZE test_runs"))
        await s.execute(text("ANALYZE test_cases"))
        expected = [str(r) for r in (await s.execute(
            select(Result.id).join(Run, Run.id == Result.test_run_id)
            .where(Run.project_id == world.a.id, Result.test_fingerprint == world.fp)
            .order_by(Run.created_at.desc(), Result.id.desc()).limit(150)
        )).scalars()]
    captured: list[tuple[str, object]] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters))

    event.listen(world.engine.sync_engine, "before_cursor_execute", _record)
    try:
        pages, cursor = [], None
        for _ in range(3):
            page = await _timeline(world, kinds=["automated_execution"], cursor=cursor)
            pages.append(page)
            cursor = page["next_cursor"]
    finally:
        event.remove(world.engine.sync_engine, "before_cursor_execute", _record)
    assert len(pages[0]["items"]) == 50 and pages[0]["next_cursor"]
    assert [e["id"] for p in pages for e in p["items"]] == expected          # no duplicate, no gap
    sources = [(q, p) for q, p in captured if "FROM test_cases JOIN test_runs" in q]
    assert len(sources) == 3
    assert all("LIMIT" in q and 51 in tuple(p) for q, p in sources)


async def test_every_kind_with_its_actor_and_only_the_latest_execution_versioned(world):
    body = await _timeline(world)
    by_kind: dict[str, list[dict]] = {}
    for entry in body["items"]:
        by_kind.setdefault(entry["kind"], []).append(entry)
    executions = by_kind["plan_execution"]
    assert [(e["status"], e["version"]) for e in executions] == [("passed", 3), ("failed", None)]
    assert executions[0]["title"] == "Executed in plan Sprint 42 · PASSED (v3)"
    assert executions[1]["title"] == "Executed in plan Sprint 42 · FAILED"
    assert all(e["actor_name"] == "W13 Alice" and e["link"] == {"type": "plan", "plan_id": world.plan.id}
               for e in executions)
    assert [(e["title"], e["actor_name"]) for e in by_kind["review_requested"]] == [("Review requested", "W13 Alice")]
    assert [(e["title"], e["actor_name"], e["detail"]) for e in by_kind["review_decided"]] == [
        ("Approved", "W13 Lead", "Looks right"),
    ]
    assert [(e["detail"], e["actor_name"]) for e in by_kind["comment"]] == [("<script>x</script>", "W13 Lead")]
    assert [(e["title"], e["actor_source"]) for e in by_kind["version"]] == [("Created as v1 (person)", "person")]
    assert [e["actor_name"] for e in by_kind["requirement"]] == ["W13 Alice"]
    # 3 runs, 1 defect, 2 executions, a request, a decision, a comment, a version, a requirement event
    assert len(body["items"]) == 3 + 1 + 2 + 1 + 1 + 1 + 1 + 1
    stamps = [e["occurred_at"] for e in body["items"]]
    assert stamps == sorted(stamps, reverse=True)


def _client(world, user):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/test-management")

    async def _db():
        async with world.factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_through_the_route_another_projects_case_is_403(world):
    async with world.factory() as s:
        alice = await s.get(User, world.alice.id)
    async with _client(world, alice) as client:
        mine = await client.get(f"/api/v1/test-management/cases/{world.case.id}/timeline",
                                params=[("kinds", "comment"), ("kinds", "version")])
        theirs = await client.get(f"/api/v1/test-management/cases/{world.b_case.id}/timeline")
        unknown = await client.get(f"/api/v1/test-management/cases/{uuid.uuid4()}/timeline")
    assert mine.status_code == 200, mine.text
    assert mine.json()["kinds"] == ["version", "comment"]
    assert [e["kind"] for e in mine.json()["items"]] == ["comment", "version"]
    assert theirs.status_code == 403 and theirs.json()["detail"] == "You do not have access to this project"
    assert unknown.status_code == 404 and unknown.json()["detail"] == "Test case not found"
