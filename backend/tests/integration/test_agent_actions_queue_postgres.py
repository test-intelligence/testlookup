"""The agent-action queue and its PATCH through the app on real PostgreSQL (W15 contract §2.1 test 2).

What only SQL shows: ``count(*) OVER ()`` as ``total`` under a page limit; the
queue orders; QUERY ids applied INSIDE the path project (a foreign pipeline id
matches nothing, another project's action id is 404 -- no oracle); the newest
report review per pipeline; the statement budget (no statement per row); a
project-bound API key lists its own project and cannot decide; the decision,
its audit row and its activity row commit together or not at all; notes never
come back; and the list and the PATCH (the live Act gate included) run on ONE
connection -- the request never opens a second session
(``tests/regression/test_no_second_session_while_the_request_holds_one.py``'s
rule; that harness takes no route by parameter, so the proof lives here).

Uses ``tests/integration/defect_w06_world.py`` (it awaits
``dispose_engine_for_loop()`` before building its engine and after disposing
it, and closes its Redis client); this module's fixture also closes the app's
Redis / Mongo clients (the PATCH's pipeline event binds Mongo). Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head and ``REDIS_URL``.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tests.integration.defect_w06_world import StatementCounter, defect_world, env

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _ledger(project_id, *, action_type, status, created_at, payload, **fields):
    from app.models.postgres import AgentActionLedger
    from app.services.canonical_json import stable_json_sha256

    return AgentActionLedger(
        id=uuid.uuid4(), project_id=project_id, action_type=action_type,
        target_type=fields.pop("target_type", "test"), target_id=fields.pop("target_id", "fpA1b2C3d4"),
        status=status, approval_required=fields.pop("approval_required", True),
        idempotency_key=f"k{uuid.uuid4().hex[:20]}", request_sha256=stable_json_sha256(payload, max_bytes=128_000),
        request_payload=payload, created_at=created_at, **fields,
    )


def _quarantine(run_id, mode="act"):
    return {
        "proposing_agent_id": "investigator", "proposing_agent_mode": mode,
        "investigation_id": str(uuid.uuid4()), "test_run_id": str(run_id),
        "test_fingerprint": "fpA1b2C3d4", "test_name": "checkout retry", "suite_name": "checkout-api",
        "title": "Quarantine checkout retry", "rationale": "Failing in this run and already flaky.",
        "risk": "low", "evidence": [],
    }


def _acknowledge(item):
    return {"proposing_agent_id": "agent.decision_report.v1", "action_id": item, "title": f"Confirm {item}"}


async def _seed(w, db):
    """Leads, a Q run, three pipelines with their reviews and the ledger rows (timestamps built HERE)."""
    from app.models.postgres import (
        AgentPipelineRun, ApiKey, LaunchStatus, ProjectMember, ReviewRequest, TestRun, UserRole,
    )
    from app.services.review_request_service import AI_DISCLAIMER_VERSION

    now = datetime.now(timezone.utc)
    w.lead_id = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Lead Requester")
    w.lead2_id = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Lead Approver")
    w.viewer_id = await w.user(db, role=UserRole.VIEWER.value)
    for uid, role in ((w.lead_id, UserRole.QA_LEAD), (w.lead2_id, UserRole.QA_LEAD), (w.viewer_id, UserRole.VIEWER)):
        db.add(ProjectMember(project_id=w.P, user_id=uid, role=role.value))
    w.run_q = uuid.uuid4()
    db.add(TestRun(id=w.run_q, project_id=w.Q, build_number=f"w15b-q-{w.tag}", jenkins_job="w15b",
                   status=LaunchStatus.FAILED, ingestion_source="unknown", total_tests=1, failed_tests=1))
    await db.flush()
    w.pipe_acc, w.pipe_pend, w.pipe_q = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for pid, run, requester, workflow in (
        (w.pipe_acc, w.run_r, w.lead_id, "investigation"), (w.pipe_pend, w.run_r, w.lead_id, "deep"),
        (w.pipe_q, w.run_q, None, "investigation"),
    ):
        db.add(AgentPipelineRun(id=pid, test_run_id=run, requested_by=requester, workflow_type=workflow,
                                status="completed"))
    await db.flush()
    for pid, project, run, state in ((w.pipe_acc, w.P, w.run_r, "accepted"), (w.pipe_pend, w.P, w.run_r, "pending_review")):
        db.add(ReviewRequest(
            project_id=project, kind="report", subject_type="pipeline_run", subject_id=str(pid),
            pipeline_run_id=pid, test_run_id=run, state=state, ai_disclaimer_version=AI_DISCLAIMER_VERSION,
            reviewed_at=now if state == "accepted" else None,
        ))
    rows = {
        "A": _ledger(w.P, action_type="quarantine.apply", status="pending_review", created_at=now - timedelta(minutes=50),
                     payload=_quarantine(w.run_r), test_run_id=w.run_r, pipeline_run_id=w.pipe_acc),
        "B": _ledger(w.P, action_type="decision_report_action", status="pending_review",
                     created_at=now - timedelta(minutes=40), payload=_acknowledge("gate-b1"),
                     target_type="release_item", target_id="gate-b1", test_run_id=w.run_r, pipeline_run_id=w.pipe_pend),
        "C": _ledger(w.P, action_type="quarantine.apply", status="pending_review", created_at=now - timedelta(minutes=30),
                     payload=_quarantine(w.run_r), test_run_id=w.run_r),
        "T1": _ledger(w.P, action_type="tool_call", status="executing", approval_required=False,
                      created_at=now - timedelta(minutes=20), updated_at=now - timedelta(minutes=20),
                      payload={"tool": "a"}, test_run_id=w.run_r, pipeline_run_id=w.pipe_pend),
        "T2": _ledger(w.P, action_type="tool_call", status="executing", approval_required=False,
                      created_at=now - timedelta(minutes=10), updated_at=now - timedelta(minutes=10),
                      payload={"tool": "b"}, test_run_id=w.run_r, pipeline_run_id=w.pipe_pend),
        "D1": _ledger(w.P, action_type="quarantine.apply", status="rejected", created_at=now - timedelta(minutes=60),
                      payload=_quarantine(w.run_r), approved_by=w.lead2_id, approved_at=now - timedelta(minutes=15),
                      result_payload={"rejection": {"reason_code": "other", "notes": "zebra private remark"}}),
        "D2": _ledger(w.P, action_type="decision_report_action", status="executed",
                      created_at=now - timedelta(minutes=70), payload=_acknowledge("gate-d2"), target_type="release_item",
                      approved_by=w.lead2_id, approved_at=now - timedelta(minutes=12),
                      execution_completed_at=now - timedelta(minutes=5), result_payload={"acknowledged": True}),
        "XQ": _ledger(w.Q, action_type="quarantine.apply", status="pending_review", created_at=now,
                      payload=_quarantine(w.run_q), test_run_id=w.run_q, pipeline_run_id=w.pipe_q),
    }
    for row in rows.values():
        db.add(row)
    w.raw_key = f"qai_{secrets.token_urlsafe(32)}"
    db.add(ApiKey(id=uuid.uuid4(), user_id=w.lead_id, name=f"w15b {w.tag}",
                  key_hash=hashlib.sha256(w.raw_key.encode()).hexdigest(),
                  key_hint=w.raw_key[:8] + "...", scopes=[], project_id=w.P, expires_at=None))
    await db.flush()
    w.ids = {name: row.id for name, row in rows.items()}


async def _turn_act_on(w, ticked=("quarantine.apply",)):
    """The investigator in Act with ``ticked`` allowed, through the real PUT (QA_LEAD, If-Match)."""
    base = f"/api/v1/projects/{w.P}/agent-configs/investigator"
    current = await w.client.get(base, headers=w.lead2)
    assert current.status_code == 200, current.text
    document = current.json()["config"]
    document["mode"] = "act"
    extensions = document.setdefault("extensions", {}) or {}
    investigator = extensions.get("investigator") or {}
    investigator["act"] = {"allowed_action_types": list(ticked)}
    extensions["investigator"] = investigator
    document["extensions"] = extensions
    saved = await w.client.put(
        base, json=document, headers={**w.lead2, "If-Match": str(current.json()["config_version"])},
    )
    assert saved.status_code == 200, saved.text


@pytest.fixture
async def world():
    env("REDIS_URL")
    from app.db.mongo import close_mongo
    from app.db.redis_client import close_redis

    try:
        async with defect_world() as w:
            async with w.sessions.begin() as db:
                await _seed(w, db)
            w.lead, w.lead2, w.viewer = w.jwt(w.lead_id), w.jwt(w.lead2_id), w.jwt(w.viewer_id)
            w.key = {"X-API-Key": w.raw_key}
            await _turn_act_on(w)
            yield w
    finally:
        # The PATCH's best-effort pipeline event binds the app's Mongo client; close both
        # app clients so the next module never meets "Event loop is closed".
        await close_redis()
        await close_mongo()


def _base(w, project=None) -> str:
    return f"/api/v1/projects/{project or w.P}/agent-actions"


def _by_id(answer) -> dict:
    return {item["id"]: item for item in answer["items"]}


async def _rows(w, model, **where):
    async with w.sessions() as db:
        stmt = select(model)
        for name, value in where.items():
            stmt = stmt.where(getattr(model, name) == value)
        return list((await db.execute(stmt)).scalars().all())


# ── the list ─────────────────────────────────────────────────────────────────


async def test_the_pending_view_counts_the_filtered_set_and_runs_oldest_first(world):
    w = world
    page = await w.client.get(_base(w), params={"view": "pending", "limit": 2}, headers=w.lead2)
    assert page.status_code == 200, page.text
    body = page.json()
    # 3 pending + 2 tool-call ``executing`` rows + decided rows in P, one pending row in Q:
    assert (body["total"], body["count"]) == (3, 2)
    assert [i["id"] for i in body["items"]] == [str(w.ids["A"]), str(w.ids["B"])]

    full = (await w.client.get(_base(w), params={"view": "pending", "limit": 200}, headers=w.lead2)).json()
    assert [i["id"] for i in full["items"]] == [str(w.ids[k]) for k in ("A", "B", "C")]
    items = _by_id(full)
    a, b, c = (items[str(w.ids[k])] for k in ("A", "B", "C"))
    assert (a["proposing_review_state"], b["proposing_review_state"], c["proposing_review_state"]) == (
        "accepted", "pending_review", "none")
    assert a["review_id"] and c["review_id"] is None
    assert a["test_run_build_number"] and a["pipeline_workflow_type"] == "investigation"
    assert a["approvable"] is True and a["approve_blocked_reason"] is None
    assert b["approvable"] is False and b["approve_blocked_reason"].startswith("Accept the AI report first")
    assert b["canonical_type"] == "decision.acknowledge" and b["proposing_agent_label"] == "Decision report"


async def test_the_requester_sees_its_own_investigations_effects_blocked(world):
    w = world
    items = _by_id((await w.client.get(_base(w), params={"view": "pending"}, headers=w.lead)).json())
    a = items[str(w.ids["A"])]
    assert a["approvable"] is False
    assert a["approve_blocked_reason"] == (
        "The person who requested this investigation cannot approve the changes it proposes.")


async def test_a_foreign_pipeline_or_run_id_in_the_query_matches_nothing(world):
    w = world
    for params in ({"pipeline_run_id": str(w.pipe_q)}, {"test_run_id": str(w.run_q)}):
        answer = await w.client.get(_base(w), params=params, headers=w.lead2)
        assert answer.status_code == 200, answer.text  # no 403 / 404 oracle
        assert (answer.json()["items"], answer.json()["total"]) == ([], 0)
    own = (await w.client.get(_base(w), params={"pipeline_run_id": str(w.pipe_acc)}, headers=w.lead2)).json()
    assert [i["id"] for i in own["items"]] == [str(w.ids["A"])]


async def test_the_action_type_filter_matches_its_alias(world):
    w = world
    for spelling in ("decision.acknowledge", "decision_report_action"):
        answer = (await w.client.get(_base(w), params={"action_type": spelling}, headers=w.lead2)).json()
        assert {i["id"] for i in answer["items"]} == {str(w.ids["B"]), str(w.ids["D2"])}, spelling


async def test_the_decided_view_runs_newest_decision_first_and_never_returns_notes(world):
    w = world
    answer = await w.client.get(_base(w), params={"view": "decided"}, headers=w.lead2)
    assert answer.status_code == 200, answer.text
    assert [i["id"] for i in answer.json()["items"]] == [str(w.ids[k]) for k in ("D2", "T2", "D1", "T1")]
    assert "zebra private remark" not in answer.text and '"notes"' not in answer.text
    items = _by_id(answer.json())
    d1, d2 = items[str(w.ids["D1"])], items[str(w.ids["D2"])]
    assert d1["rejection_reason_code"] == "other" and d1["outcome_text"] == "Rejected by Lead Approver (other)."
    assert (d2["outcome"], d2["outcome_text"]) == ("acknowledged", "Acknowledged by Lead Approver.")


async def test_the_list_spends_no_statement_per_row(world):
    from app.services.agent_action_queue_service import list_actions

    w = world
    async with w.sessions() as db:
        with StatementCounter(w.engine) as decided:
            await list_actions(db, project_id=w.P, view="decided")
    assert len(decided.statements) <= 3, decided.statements  # the page + the reviews

    async def _pending(limit):
        async with w.sessions() as db:
            viewer = SimpleNamespace(id=w.lead2_id)
            with StatementCounter(w.engine) as counter:
                await list_actions(db, project_id=w.P, view="pending", limit=limit, viewer=viewer)
        return counter.statements

    one, three = await _pending(1), await _pending(200)
    for statements in (one, three):
        assert sum("FROM agent_action_ledger" in s for s in statements) == 1, statements
        # the proposing-report read (the Act gate's drift-pin check reads review_requests too)
        assert sum("review_requests.pipeline_run_id IN" in s for s in statements) == 1, statements
    # Three pending rows cost no more than one (the AI-config cache may make the later call cheaper).
    assert len(three) <= len(one), "a statement per row: the Act gate must run once per page, not per row"


# ── the PATCH ────────────────────────────────────────────────────────────────


async def test_another_projects_action_through_this_path_is_not_found(world):
    from app.models.postgres import AgentActionLedger

    w = world
    answer = await w.client.patch(f"{_base(w)}/{w.ids['XQ']}", json={"status": "approved"}, headers=w.lead2)
    assert answer.status_code == 404, answer.text
    assert answer.json()["detail"]["code"] == "action_not_found"
    assert [r.status for r in await _rows(w, AgentActionLedger, id=w.ids["XQ"])] == ["pending_review"]


async def test_a_project_bound_key_lists_its_own_project_and_cannot_decide(world):
    w = world
    assert (await w.client.get(_base(w), params={"view": "pending"}, headers=w.key)).status_code == 200
    assert (await w.client.get(_base(w, w.Q), headers=w.key)).status_code == 403
    refused = await w.client.patch(f"{_base(w)}/{w.ids['C']}", json={"status": "approved"}, headers=w.key)
    assert refused.status_code == 403, refused.text  # the dependency: no key opt-in on the PATCH


async def _approval_writes(w, action_id):
    from app.models.postgres import AccessAuditLog, AgentActionDispatchOutbox, ProjectActivityEvent

    events = [e for e in await _rows(w, ProjectActivityEvent, project_id=w.P, event_type="agent.action_approved")
              if e.entity_id == str(action_id)]
    audits = [a for a in await _rows(w, AccessAuditLog, project_id=w.P, action="agent_action.approved")
              if (a.after_value or {}).get("action_id") == str(action_id)]
    outbox = await _rows(w, AgentActionDispatchOutbox, action_id=action_id)
    return events, audits, outbox


async def test_the_approval_its_audit_and_its_activity_commit_together_or_not_at_all(world, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.postgres import AgentActionLedger

    w = world
    real_commit = AsyncSession.commit

    async def _failing_commit(self):
        raise RuntimeError("forced commit failure")

    from httpx import ASGITransport, AsyncClient

    from app.main import app

    monkeypatch.setattr(AsyncSession, "commit", _failing_commit)
    try:
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                               base_url="http://testserver") as client:
            failed = await client.patch(f"{_base(w)}/{w.ids['A']}", json={"status": "approved"}, headers=w.lead2)
    finally:
        monkeypatch.setattr(AsyncSession, "commit", real_commit)
    assert failed.status_code == 500
    assert await _approval_writes(w, w.ids["A"]) == ([], [], [])
    assert [r.status for r in await _rows(w, AgentActionLedger, id=w.ids["A"])] == ["pending_review"]

    approved = await w.client.patch(f"{_base(w)}/{w.ids['A']}", json={"status": "approved"}, headers=w.lead2)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved" and approved.json()["approved_by_name"] == "Lead Approver"
    events, audits, outbox = await _approval_writes(w, w.ids["A"])
    assert (len(events), len(audits), len(outbox)) == (1, 1, 1)
    assert events[0].context["canonical_type"] == "quarantine.apply"
    assert audits[0].actor_user_id == w.lead2_id


async def test_the_requester_is_refused_and_nothing_is_written(world):
    w = world
    refused = await w.client.patch(f"{_base(w)}/{w.ids['A']}", json={"status": "approved"}, headers=w.lead)
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"]["code"] == "separation_of_duties"
    assert await _approval_writes(w, w.ids["A"]) == ([], [], [])


async def test_a_rejection_stores_redacted_notes_and_never_returns_them(world):
    from app.models.postgres import AgentActionLedger, ProjectActivityEvent

    w = world
    notes = "Not flaky, see the ticket. Ping qa.owner@example.com"
    answer = await w.client.patch(f"{_base(w)}/{w.ids['B']}", headers=w.lead2,
                                  json={"status": "rejected", "reason_code": "unsupported_claim", "notes": notes})
    assert answer.status_code == 200, answer.text
    assert "Not flaky" not in answer.text and answer.json()["rejection_reason_code"] == "unsupported_claim"
    [row] = await _rows(w, AgentActionLedger, id=w.ids["B"])
    assert row.status == "rejected" and row.result_payload["rejection"]["notes"].startswith("Not flaky")
    assert "qa.owner@example.com" not in row.result_payload["rejection"]["notes"]
    [event] = [e for e in await _rows(w, ProjectActivityEvent, project_id=w.P, event_type="agent.action_denied")
               if e.entity_id == str(w.ids["B"])]
    assert event.context["reason_code"] == "unsupported_claim" and "Not flaky" not in str(event.context)


# ── every GET answer is 200 / 403 / 404 / 422, never 500 ─────────────────────


@pytest.mark.parametrize("who, params, expected", [
    ("admin", {"view": "nonsense"}, 422),
    ("admin", {"view": "pending", "status": "approved"}, 422),
    ("admin", {"status": "bogus"}, 422),
    ("admin", {"pipeline_run_id": "not-a-uuid"}, 422),
    ("admin", {"view": "decided", "action_type": "quarantine.apply", "test_run_id": str(uuid.UUID(int=7))}, 200),
    ("viewer", {"view": "pending"}, 403),
    ("viewer", {"view": "nonsense"}, 403),
])
async def test_the_list_answers_every_query_without_a_500(world, who, params, expected):
    w = world
    answer = await w.client.get(_base(w), params=params, headers=getattr(w, who))
    assert answer.status_code == expected, answer.text
    if expected == 422 and "view" in params and "pipeline_run_id" not in params:
        assert answer.json()["detail"]["message"] in (
            "view must be pending or decided.", "Send view or status, not both.")


async def test_the_investigation_detail_of_an_unknown_id_is_404_never_500(world):
    w = world
    answer = await w.client.get(f"/api/v1/investigations/{uuid.uuid4()}", headers=w.admin)
    assert answer.status_code == 404, answer.text


# ── ONE connection: the request never opens a second session ────────────────


async def test_the_list_and_the_patch_run_on_one_connection(world, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.postgres import get_db
    from app.main import app

    w = world
    engine = create_async_engine(env("TESTLOOKUP_POSTGRES_TEST_DSN"), pool_size=1, max_overflow=0, pool_timeout=3)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def _one_connection_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    previous = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = _one_connection_db
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)  # a 2nd session would wait
    try:
        listed = await w.client.get(_base(w), params={"view": "pending"}, headers=w.lead2)
        assert listed.status_code == 200, listed.text
        assert _by_id(listed.json())[str(w.ids["C"])]["approvable"] is True  # the live Act gate ran
        approved = await w.client.patch(f"{_base(w)}/{w.ids['C']}", json={"status": "approved"}, headers=w.lead2)
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"
    finally:
        app.dependency_overrides[get_db] = previous
        await engine.dispose()
