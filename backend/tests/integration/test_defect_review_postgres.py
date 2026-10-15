"""Real PostgreSQL proof of DEF-06: the approval queue, approve-files-on-the-defect, reject-closes.

Through the real ``deep_investigation`` router on a database migrated to head,
with Jira played by the contract fake (``tests/fakes/fake_integrations.py``,
reached over ``httpx.ASGITransport``: it refuses what Jira Cloud refuses) and
the E7.6 claim ledger on its own real sessions. What a mocked session cannot
show:

* the approval is COMMITTED before Jira is called: a row read on another
  connection while the fake holds the POST already says ``approved``;
* the ticket lands on the approved defect: the project's defect count is
  unchanged, the key / URL are on that row, and the ledger rows are in
  ``project_activity_events``;
* an ACTIVE linked twin of the same signature is reused (no POST, a
  recurrence comment, ``duplicate_of``);
* reject closes the defect as INVALID with ``resolved_at`` and the gate's
  ``count_open_critical_defects`` stops counting it;
* the pending list's COUNT, scope and live-project filter in SQL.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``; skips otherwise.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.fakes.fake_integrations import FakeState, create_app

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

DOMAIN = "fake-jira.example.test"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from app.core.config import settings
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.models.postgres import Defect, Project, ProjectMember, TestCase, TestRun, User, UserRole
    from app.routers import deep_investigation
    from app.services import defect_jira_service as svc

    engine = create_async_engine(_dsn(), pool_size=6, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:8]
    pid, qid, gone = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    run_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    lead = User(id=uuid.uuid4(), email=f"lead-{tag}@example.test", username=f"lead-{tag}", full_name="Lee Lead",
                hashed_password="x", role=UserRole.QA_LEAD, is_active=True)
    cases = {name: uuid.uuid4() for name in ("filed", "committed", "twin", "refused", "offline", "reject")}
    async with sessions.begin() as db:
        db.add_all([Project(id=pid, name=f"review-{tag}", slug=f"review-{tag}", is_active=True),
                    Project(id=qid, name=f"review-q-{tag}", slug=f"review-q-{tag}", is_active=True),
                    Project(id=gone, name=f"review-gone-{tag}", slug=f"review-gone-{tag}", is_active=False),
                    lead])
        await db.flush()
        db.add_all([ProjectMember(user_id=lead.id, project_id=pid, role="QA_LEAD"),
                    ProjectMember(user_id=lead.id, project_id=gone, role="QA_LEAD")])
        db.add(TestRun(id=run_id, project_id=pid, build_number=f"b-{tag}", branch="main", status="FAILED",
                       total_tests=6, failed_tests=6, created_at=now - timedelta(hours=1),
                       start_time=now - timedelta(hours=1)))
        await db.flush()
        for name, case_id in cases.items():
            db.add(TestCase(id=case_id, test_run_id=run_id, test_fingerprint=f"{tag}-{name}",
                            test_name=f"test_{name}", suite_name="Checkout", status="FAILED",
                            error_message="AssertionError: 41 != 40", created_at=now - timedelta(minutes=30)))

    state = FakeState(base_url=f"https://{DOMAIN}")
    state.add_project("PLAT", "Platform")
    jira_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)))

    async def resolved(db):
        return {"enabled": True, "domain": DOMAIN, "email": state.jira_email, "api_token": state.jira_token,
                "default_project_key": "QA"}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions)
    monkeypatch.setattr(svc, "resolve_jira_config", resolved)
    monkeypatch.setattr(svc, "get_http_client", lambda: jira_client)
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    monkeypatch.setattr("app.services.defect_promoted_webhook.emit_defect_promoted", AsyncMock(return_value=0))

    class _Redis:
        """Membership cache: always a miss (the real lookup reads Postgres)."""

        async def get(self, key):
            return None

        async def set(self, *a, **k):
            return True

        async def delete(self, *a, **k):
            return 0

    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: _Redis())

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = FastAPI()
    app.include_router(deep_investigation.router)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: lead

    async def call(method, path, **kwargs):
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                               base_url="http://t") as client:
            return await client.request(method, f"/api/v1/deep-investigate{path}", **kwargs)

    async def pending(case: str, *, key: str | None = "PLAT", project_id=None, severity="CRITICAL", **fields):
        pe = {"policy_reasons": [f"Severity {severity} requires human review"], "source_run_id": str(run_id)}
        if key:
            pe["jira_project_key"] = key
        row = Defect(id=uuid.uuid4(), project_id=project_id or pid,
                     test_case_id=fields.pop("test_case_id", cases.get(case)),
                     cluster_id=f"cl-{case}", title=f"Pending {case}", description=f"Reviewed {case}",
                     severity=severity, resolution_status="OPEN", approval_status="pending_review",
                     policy_evaluation=pe, **fields)
        async with sessions.begin() as db:
            db.add(row)
        return row.id

    async def load(defect_id):
        async with sessions() as db:
            return (await db.execute(select(Defect).where(Defect.id == defect_id))).scalar_one()

    async def ledger(defect_id):
        async with sessions() as db:
            rows = await db.execute(text(
                "SELECT event_type, summary, context FROM project_activity_events "
                "WHERE entity_type = 'defect' AND entity_id = :e ORDER BY occurred_at"), {"e": str(defect_id)})
            return [dict(r._mapping) for r in rows]

    def issue_posts() -> int:
        return len([r for r in state.requests if r["method"] == "POST" and r["path"] == "/rest/api/3/issue"])

    async def defect_count():
        async with sessions() as db:
            return int(await db.scalar(select(func.count()).select_from(Defect).where(Defect.project_id == pid)))

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    try:
        yield SimpleNamespace(sessions=sessions, pid=pid, qid=qid, gone=gone, run_id=run_id, tag=tag, lead=lead,
                              state=state, call=call, pending=pending, load=load, ledger=ledger,
                              defect_count=defect_count, settings=settings, issue_posts=issue_posts)
    finally:
        monkeypatch.undo()
        async with engine.begin() as db:
            await db.execute(text("DELETE FROM project_activity_events WHERE project_id IN (:p, :q, :g)"),
                             {"p": pid, "q": qid, "g": gone})
            await db.execute(text("DELETE FROM agent_action_ledger WHERE project_id IN (:p, :q, :g)"),
                             {"p": pid, "q": qid, "g": gone})
            await db.execute(text("DELETE FROM defects WHERE project_id IN (:p, :q, :g)"),
                             {"p": pid, "q": qid, "g": gone})
            await db.execute(text("DELETE FROM projects WHERE id IN (:p, :q, :g)"), {"p": pid, "q": qid, "g": gone})
            await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": lead.id})
        await jira_client.aclose()
        await engine.dispose()


async def test_approve_online_files_one_issue_on_the_approved_row(world):
    target = await world.pending("filed")
    before = await world.defect_count()
    posts_before = world.issue_posts()

    response = await world.call("POST", f"/defects/{target}/review", json={"action": "approve"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["jira_status"] == "filed", body
    key = body["jira_ticket"]["key"]
    assert key.startswith("PLAT-")
    assert body["jira_url"] == f"https://{DOMAIN}/browse/{key}"
    assert body["approval_status"] == "executed"
    assert world.issue_posts() - posts_before == 1
    assert await world.defect_count() == before, "approve created a second defect row"
    row = await world.load(target)
    assert (row.jira_ticket_id, row.approval_status, row.jira_status) == (key, "executed", "Open")
    assert row.signature_fingerprint == f"{world.tag}-filed"
    assert row.policy_evaluation["jira_filing"]["status"] == "filed"
    assert [e["event_type"] for e in await world.ledger(target)] == ["defect.promoted", "defect.ticket_created"]
    assert world.state.issues[key]["fields"]["summary"] == "[TestLookup] Pending filed"


async def test_the_approval_is_committed_before_jira_is_called(world, monkeypatch):
    from app.services import defect_jira_service as svc

    target = await world.pending("committed")
    observed: list = []
    real_post = svc._jira_post

    async def _peek(cfg, path, json):
        async with world.sessions() as other:
            observed.append(await other.scalar(text("SELECT approval_status FROM defects WHERE id = :i"),
                                               {"i": target}))
        return await real_post(cfg, path, json)

    monkeypatch.setattr(svc, "_jira_post", _peek)

    response = await world.call("POST", f"/defects/{target}/review", json={"action": "approve"})

    assert response.json()["jira_status"] == "filed"
    assert observed and observed[0] == "approved", observed


async def test_an_active_linked_twin_is_reused(world):
    first = await world.pending("twin")
    assert (await world.call("POST", f"/defects/{first}/review", json={"action": "approve"})).json()["jira_status"] \
        == "filed"
    twin = await world.load(first)
    # The same failing test promoted again (an earlier cluster of the same run).
    second = await world.pending("twin", test_case_id=None, signature_fingerprint=twin.signature_fingerprint)
    posts_before = world.issue_posts()

    response = await world.call("POST", f"/defects/{second}/review", json={"action": "approve"})

    body = response.json()
    assert body["jira_status"] == "linked", body
    assert body["jira_ticket"]["key"] == twin.jira_ticket_id
    assert world.issue_posts() == posts_before
    reused = await world.load(second)
    assert (reused.jira_ticket_id, reused.duplicate_of) == (twin.jira_ticket_id, twin.id)
    assert (await world.load(first)).recurrence_count == twin.recurrence_count + 1
    assert world.state.issues[twin.jira_ticket_id]["comments"], "no recurrence comment"
    assert "defect.ticket_created" not in [e["event_type"] for e in await world.ledger(second)]


async def test_jira_refusing_keeps_the_approval(world):
    target = await world.pending("refused", key="NOPE")  # a project the fake does not have: Jira 400

    response = await world.call("POST", f"/defects/{target}/review", json={"action": "approve"})

    body = response.json()
    assert response.status_code == 200 and body["jira_status"] == "failed", body
    assert "HTTP 400" in body["jira_error"]
    row = await world.load(target)
    assert row.approval_status == "approved" and row.jira_ticket_id is None
    assert row.policy_evaluation["jira_filing"]["status"] == "failed"


async def test_offline_the_queue_row_says_not_filed(world, monkeypatch):
    from app.services import analytics_service

    monkeypatch.setattr(world.settings, "AI_OFFLINE_MODE", True)
    target = await world.pending("offline")
    requests_before = len(world.state.requests)

    response = await world.call("POST", f"/defects/{target}/review", json={"action": "approve"})

    body = response.json()
    assert (body["jira_status"], body["jira_reason"]) == ("not_filed", "offline_mode")
    assert len(world.state.requests) == requests_before
    async with world.sessions() as db:
        row = await analytics_service.defect_row(db, world.pid, target)
    assert row["jira_filing_status"] == "not_filed"


async def test_reject_closes_the_defect_and_the_gate_stops_counting_it(world):
    from app.services.metrics_service import count_open_critical_defects

    target = await world.pending("reject", key=None)
    async with world.sessions() as db:
        before = await count_open_critical_defects(db, world.pid)

    response = await world.call("POST", f"/defects/{target}/review",
                                json={"action": "reject", "reason": "duplicate of an existing issue"})

    assert response.status_code == 200, response.text
    row = await world.load(target)
    assert (row.approval_status, row.resolution_status) == ("rejected", "INVALID")
    assert row.resolved_at is not None
    async with world.sessions() as db:
        assert await count_open_critical_defects(db, world.pid) == before - 1
    (entry,) = await world.ledger(target)
    assert entry["event_type"] == "defect.updated"
    assert entry["summary"].endswith("status → Invalid (rejected in review)")
    assert entry["context"]["reason"] == "duplicate of an existing issue"


async def test_the_pending_list_counts_scopes_and_skips_deleted_projects(world):
    from app.models.postgres import Defect

    async with world.sessions.begin() as db:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(120):
            db.add(Defect(project_id=world.pid, title=f"queue {i:03d}", resolution_status="OPEN",
                          approval_status="pending_review", created_at=start + timedelta(minutes=i),
                          policy_evaluation={"requested_project_key": "OPS", "requested_run_id": str(world.run_id)}))
        db.add(Defect(project_id=world.gone, title="deleted project", resolution_status="OPEN",
                      approval_status="pending_review"))
        db.add(Defect(project_id=world.qid, title="other project", resolution_status="OPEN",
                      approval_status="pending_review"))
    async with world.sessions() as db:
        expected = int(await db.scalar(select(func.count()).select_from(Defect).where(
            Defect.project_id == world.pid, Defect.approval_status == "pending_review")))

    page = (await world.call("GET", f"/defects/pending-review?project_id={world.pid}&page=1&page_size=100")).json()

    assert page["total"] == expected >= 120
    assert len(page["items"]) == 100 and page["page_size"] == 100
    queued = [i for i in page["items"] if i["title"].startswith("queue ")]
    assert queued and all((i["jira_project_key"], i["run_id"]) == ("OPS", str(world.run_id)) for i in queued)
    unpinned = (await world.call("GET", "/defects/pending-review?page_size=100")).json()
    assert unpinned["total"] == expected   # the deleted project's row is excluded; Q is not the lead's
    assert (await world.call("GET", f"/defects/pending-review?project_id={world.qid}")).status_code == 403
    assert (await world.call("GET", "/defects/pending-review?project_id=nope")).status_code == 400
    gone = (await world.call("GET", f"/defects/pending-review?project_id={world.gone}")).json()
    assert gone["total"] == 0
