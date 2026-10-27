"""W21 ACT-01 slice (c): test management, agent proposals, exports and suite sync write activity rows.

"Declaration is not emission": each test drives the real producer against a real database and reads
``project_activity_events`` back:

* ``audit_event`` mirrors test-case and test-plan audit rows into the feed (created, updated, approved, deprecated as
  the delete, a plain lifecycle move as ``status_changed``), pointing ``source_table`` / ``source_id`` at the audit row;
* an agent's NEW action proposal is ``agent.fix_proposed``; the idempotent replay and a person's own proposal are not;
* a run PDF export is ``report.exported``, a share link ``report.shared`` (never the token);
* a compliance pack generation is ``compliance_pack.generated`` with the release attached;
* a suite sync that removed tests writes ONE ``suite.sync_deleted`` row per suite, linked to the catalogue suite.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database at head; skips otherwise.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def world(monkeypatch):
    import app.db.postgres as pg
    from app.models.postgres import (
        AgentActionDispatchOutbox,
        AgentActionLedger,
        ManagedTestCase,
        Project,
        ProjectActivityEvent,
        Release,
        ReportShareLink,
        TestCaseAuditLog,
        TestPlan,
        TestRun,
        TestSuite,
        User,
        UserRole,
    )

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(pg, "AsyncSessionLocal", factory, raising=False)

    marker = "zqx" + uuid.uuid4().hex[:8].replace("a", "q")
    lead = User(id=uuid.uuid4(), email=f"lead-{marker}@example.test", username=f"lead-{marker}",
                full_name="Priya Lead", hashed_password="x", role=UserRole.ADMIN, is_active=True)
    project = Project(id=uuid.uuid4(), name=f"Ledger {marker}", slug=f"ledger-{marker}", is_active=True)
    case = ManagedTestCase(id=uuid.uuid4(), project_id=project.id, title=f"Checkout totals {marker}")
    plan = TestPlan(id=uuid.uuid4(), project_id=project.id, name=f"Sprint plan {marker}")
    run = TestRun(id=uuid.uuid4(), project_id=project.id, build_number=f"b-{marker}", jenkins_job="ledger",
                  ingestion_source="unknown")
    release = Release(id=uuid.uuid4(), project_id=project.id, name=f"3.0.0-{marker}", status="in_progress")
    suite = TestSuite(id=uuid.uuid4(), project_id=project.id, name=f"checkout-{marker}")
    async with factory() as db:
        db.add_all([lead, project])
        await db.flush()
        db.add_all([case, plan, run, release, suite])
        await db.commit()
    try:
        yield SimpleNamespace(factory=factory, lead=lead, project=project, case=case, plan=plan, run=run,
                              release=release, suite=suite, marker=marker)
    finally:
        async with factory() as db:
            pid = project.id
            await db.execute(delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id == pid))
            await db.execute(delete(TestCaseAuditLog).where(TestCaseAuditLog.project_id == pid))
            await db.execute(delete(AgentActionDispatchOutbox).where(
                AgentActionDispatchOutbox.action_id.in_(select(AgentActionLedger.id).where(
                    AgentActionLedger.project_id == pid))))
            for model in (AgentActionLedger, ReportShareLink, ManagedTestCase, TestPlan, TestSuite, TestRun, Release):
                await db.execute(delete(model).where(model.project_id == pid))
            await db.execute(delete(Project).where(Project.id == pid))
            await db.commit()
        async with factory() as db:
            try:
                await db.execute(delete(User).where(User.id == lead.id))
                await db.commit()
            except Exception:  # an audit row may still reference the user
                await db.rollback()
        await engine.dispose()


async def _rows(w, prefix: str = ""):
    from app.models.postgres import ProjectActivityEvent

    async with w.factory() as db:
        stmt = select(ProjectActivityEvent).where(ProjectActivityEvent.project_id == w.project.id)
        if prefix:
            stmt = stmt.where(ProjectActivityEvent.event_type.startswith(prefix))
        rows = (await db.execute(stmt.order_by(ProjectActivityEvent.occurred_at))).scalars().all()
    for row in rows:
        assert "—" not in row.summary, f"unfilled placeholder in {row.event_type}: {row.summary}"
    return rows


async def test_audit_event_mirrors_test_case_and_plan_rows(world):
    w = world
    from app.models.postgres import TestCaseAuditLog
    from app.services.test_management_audit_service import audit_event

    pid, cid = w.project.id, w.case.id
    steps = [
        ("test_case", cid, "created", {}),
        ("test_case", cid, "updated", {"new_values": {"priority": "high", "title": "x"}}),
        ("test_case", cid, "request_review", {"transition_from": "draft", "transition_to": "in_review"}),
        ("test_case", cid, "approve", {"transition_from": "in_review", "transition_to": "approved"}),
        ("test_case", cid, "claim_review", {"transition_from": "in_review", "transition_to": "in_review"}),
        ("test_case", cid, "deprecate", {"transition_from": "approved", "transition_to": "deprecated"}),
        ("test_plan", w.plan.id, "created", {}),
        ("test_plan", w.plan.id, "updated", {"new_values": {"name": "y"}}),
        ("test_plan_item", w.plan.id, "added", {}),
    ]
    for entity_type, eid, action, extra in steps:
        async with w.factory() as db:
            await audit_event(db, entity_type, eid, pid, action, w.lead, **extra)
            await db.commit()

    rows = await _rows(w, "test_")
    assert [(x.event_type, x.summary) for x in rows] == [
        ("test_case.created", f"Test case {w.case.title} was created"),
        ("test_case.updated", f"Test case {w.case.title} changed: priority, title"),
        ("test_case.status_changed", f"Test case {w.case.title} moved to in_review"),
        ("test_case.approved", f"Test case {w.case.title} was approved"),
        ("test_case.deleted", f"Test case {w.case.title} was deleted"),
        ("test_plan.created", f"Test plan {w.plan.name} was created"),
        ("test_plan.updated", f"Test plan {w.plan.name} changed: name"),
    ]
    async with w.factory() as db:
        audit_ids = set((await db.execute(select(TestCaseAuditLog.id).where(
            TestCaseAuditLog.project_id == pid))).scalars().all())
    assert all(x.source_table == "test_case_audit_logs" and x.source_id in audit_ids for x in rows)
    assert all(x.actor_id == w.lead.id for x in rows)


async def test_a_new_agent_proposal_is_fix_proposed_once(world):
    w = world
    from app.services.agent_action_ledger_service import create_action_proposal

    payload = {"proposing_agent_id": "agent.decision_report.v1", "action_id": "a-1", "title": "Pin the flaky retry",
               "risk": "low", "evidence": []}
    kwargs = dict(project_id=w.project.id, action_type="decision_report_action", target_type="human_review",
                  target_id="a-1", request_payload=payload, approval_required=True)
    async with w.factory() as db:
        await create_action_proposal(db, idempotency_key=f"k1-{w.marker}", **kwargs)
        await db.commit()
    async with w.factory() as db:  # the idempotent replay
        await create_action_proposal(db, idempotency_key=f"k1-{w.marker}", **kwargs)
        await db.commit()
    async with w.factory() as db:  # a person's own proposal
        await create_action_proposal(db, idempotency_key=f"k2-{w.marker}", actor_user_id=w.lead.id, **kwargs)
        await db.commit()

    rows = await _rows(w, "agent.")
    assert [x.event_type for x in rows] == ["agent.fix_proposed"]
    assert rows[0].actor_type == "agent"
    assert rows[0].summary.endswith("proposed a fix for " + rows[0].entity_label)


async def test_exports_shares_and_compliance_packs(world, monkeypatch):
    w = world
    from app.models.schemas import CompliancePackGenerateRequest
    from app.routers import compliance_packs as cp
    from app.routers import reports as r

    async with w.factory() as db:
        await r._record_export(db, w.lead, w.project.id, w.run.id, f"Run {str(w.run.id)[:8]} PDF (executive)",
                               {"format": "pdf"})
        await db.commit()
    async with w.factory() as db:
        out = await r.create_share_link_endpoint(w.run.id, r.CreateShareLinkRequest(), db=db, current_user=w.lead)

    async def _pack(db, release, **_k):
        return SimpleNamespace(id=uuid.uuid4(), project_id=release.project_id)

    monkeypatch.setattr(cp.svc, "generate_pack", _pack)
    async with w.factory() as db:
        await cp.generate_compliance_pack(w.release.id, CompliancePackGenerateRequest(), current_user=w.lead, db=db,
                                          _=w.lead)
        await db.commit()

    rows = await _rows(w)
    assert [x.event_type for x in rows] == ["report.exported", "report.shared", "compliance_pack.generated"]
    assert rows[0].summary == f"Report Run {str(w.run.id)[:8]} PDF (executive) was exported"
    assert rows[1].summary == f"Report Run {str(w.run.id)[:8]} (executive) was shared"
    assert out.token and out.token not in str(rows[1].context)
    assert rows[2].summary == f"Compliance pack for release {w.release.name} was generated"
    assert rows[2].release_id == w.release.id


async def test_suite_sync_deletions_write_one_row_per_suite(world):
    w = world
    from app.services.suite_sync_service import _record_sync_deletions

    summaries = [
        {"suite_name": w.suite.name, "deleted_count": 3},
        {"suite_name": "no-catalogue-suite", "deleted_count": 1},
        {"suite_name": "untouched", "deleted_count": 0},
    ]
    async with w.factory() as db:
        await _record_sync_deletions(db, w.project.id, w.run.id, summaries)
        await db.commit()

    rows = await _rows(w, "suite.")
    assert [x.summary for x in rows] == [
        f"3 tests were removed from suite {w.suite.name}",
        "1 tests were removed from suite no-catalogue-suite",
    ]
    assert rows[0].entity_id == str(w.suite.id) and rows[0].actor_type == "system"
