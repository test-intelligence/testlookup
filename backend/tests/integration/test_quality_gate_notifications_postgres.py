"""W09 NTF-01 on real PostgreSQL: "Quality gate failed" through the app.

11. Project P, release R. Subscribers holding ``quality_gate_failed``: two
    members (one project-scoped preference, one all-projects), an admin who is
    not a member (receives), a former member's all-projects preference and an
    inactive member's (receive nothing). A QA lead's
    ``POST /releases/R/gate/evaluate`` with the rollup answering NO_GO stages
    exactly three ``notification_logs`` rows (``pending``, ``run_id`` NULL,
    ``delivery_metadata->>'app_path' = '/releases/R'``, titles naming R); the
    first member's ``GET /notifications/history`` item links to ``/releases/R``.
12. Evaluating again while NO_GO adds nothing; GO then NO_GO adds three more.
    A preview (``record=false``) adds nothing.
13. A phase gate GO -> NO_GO: the titles carry `` · <phase>``.
14. An override GO -> NO_GO stages rows under the run with the override
    delivery key; the agent path over another run with the review gate
    enforced and no accepted review answers ``withheld`` and stages nothing,
    and without enforcement stages once however often it is asked.

Uses ``tests/integration/defect_w06_world.py``. Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (0221) and ``REDIS_URL``.
"""
from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import select, text

from tests.integration.defect_w06_world import defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

EVENT = "quality_gate_failed"
REASON = "pay is FAILED"


@pytest.fixture
async def world(monkeypatch):
    from app.models.postgres import (
        LaunchStatus,
        NotificationPreference,
        ProjectMember,
        ReleaseDecision,
        ReleasePhase,
        TestRun,
        UserRole,
    )
    from app.services import release_rollup_service as rollup_svc

    async with defect_world() as w:
        async with w.sessions.begin() as db:
            w.lead_id = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Priya Nair")
            w.second_id = await w.user(db, role=UserRole.QA_ENGINEER.value)
            w.admin2_id = await w.user(db, role=UserRole.ADMIN.value)
            w.former_id = await w.user(db, role=UserRole.QA_ENGINEER.value)
            w.inactive_id = await w.user(db, role=UserRole.QA_ENGINEER.value, is_active=False)
            for uid, role in ((w.lead_id, UserRole.QA_LEAD), (w.second_id, UserRole.QA_ENGINEER),
                              (w.inactive_id, UserRole.QA_ENGINEER)):
                db.add(ProjectMember(project_id=w.P, user_id=uid, role=role.value))
            for uid, project in ((w.member_id, w.P), (w.second_id, None), (w.admin2_id, None),
                                 (w.former_id, None), (w.inactive_id, w.P)):
                db.add(NotificationPreference(user_id=uid, project_id=project, channel="email", enabled=True,
                                              events=[EVENT, "run_failed"]))
            w.phase_id = uuid.uuid4()
            db.add(ReleasePhase(id=w.phase_id, release_id=w.R, name="Staging", phase_type="staging"))
            db.add(ReleaseDecision(test_run_id=w.run_r, recommendation="GO", risk_score=40,
                                   blocking_issues=["pay is broken"]))
            w.run_agent = uuid.uuid4()
            db.add(TestRun(id=w.run_agent, project_id=w.P, build_number=f"agent-{w.tag}", jenkins_job="w09a",
                           status=LaunchStatus.FAILED, ingestion_source="unknown", total_tests=1, failed_tests=1))
            await db.flush()
            db.add(ReleaseDecision(test_run_id=w.run_agent, recommendation="NO_GO", risk_score=90,
                                   blocking_issues=["checkout is down"]))
        w.lead = w.jwt(w.lead_id)
        w.recipients = {w.member_id, w.second_id, w.admin2_id}
        w.verdict = "NO_GO"
        monkeypatch.setattr(rollup_svc, "decide", lambda rollup, **kw: (w.verdict, [REASON] if w.verdict == "NO_GO" else []))
        try:
            yield w
        finally:
            async with w.sessions.begin() as db:
                await db.execute(text("DELETE FROM notification_logs WHERE project_id = :p"), {"p": w.P})


async def _rows(w):
    from app.models.postgres import NotificationLog

    async with w.sessions() as db:
        return list((await db.execute(
            select(NotificationLog)
            .where(NotificationLog.project_id == w.P, NotificationLog.event_type == EVENT)
            .order_by(NotificationLog.created_at)
        )).scalars().all())


async def _evaluate(w, verdict, *, record=True, phase=False):
    w.verdict = verdict
    path = f"/api/v1/releases/{w.R}" + (f"/phases/{w.phase_id}" if phase else "") + "/gate/evaluate"
    response = await w.client.post(path, params={"record": str(record).lower()}, headers=w.lead)
    assert response.status_code == 200, response.text
    assert response.json()["verdict"] == verdict
    return response.json()


def _key(scope: str, user_id, channel="email") -> str:
    return hashlib.sha256(f"{scope}:{user_id}:{channel}:{EVENT}".encode("utf-8")).hexdigest()


async def test_a_transition_into_no_go_stages_one_entry_per_subscriber_and_links_the_release(world):
    w = world
    result = await _evaluate(w, "NO_GO")
    rows = await _rows(w)
    assert {r.user_id for r in rows} == w.recipients
    assert len(rows) == 3
    release_name = f"w06a-r-{w.tag}"
    scope = f"quality-gate-failed:gate:{result['decision_id']}:v1"
    for row in rows:
        assert (row.status, row.run_id, row.channel) == ("pending", None, "email")
        assert row.delivery_metadata["app_path"] == f"/releases/{w.R}"
        assert row.delivery_metadata["decision_id"] == result["decision_id"]
        assert row.title == f"Quality gate failed: {release_name} — {REASON}"
        assert row.body == f"• {REASON}"
        assert row.delivery_key == _key(scope, row.user_id)

    history = await w.client.get("/api/v1/notifications/history", headers=w.member)
    assert history.status_code == 200, history.text
    [item] = [i for i in history.json() if i["event_type"] == EVENT]
    assert item["link"] == f"/releases/{w.R}"


async def test_a_repeat_adds_nothing_and_a_new_transition_notifies_again(world):
    w = world
    await _evaluate(w, "NO_GO")
    await _evaluate(w, "NO_GO")
    await _evaluate(w, "NO_GO", record=False)
    assert len(await _rows(w)) == 3
    await _evaluate(w, "GO")
    assert len(await _rows(w)) == 3
    await _evaluate(w, "NO_GO")
    rows = await _rows(w)
    assert len(rows) == 6
    assert len({r.delivery_key for r in rows}) == 6


async def test_a_phase_gate_transition_names_the_phase(world):
    w = world
    await _evaluate(w, "GO", phase=True)
    assert await _rows(w) == []
    await _evaluate(w, "NO_GO", phase=True)
    rows = await _rows(w)
    assert {r.user_id for r in rows} == w.recipients
    for row in rows:
        assert row.title == f"Quality gate failed: w06a-r-{w.tag} · Staging — {REASON}"
        assert row.delivery_metadata["phase_id"] == str(w.phase_id)
        assert row.delivery_metadata["app_path"] == f"/releases/{w.R}"


async def test_an_override_into_no_go_stages_under_the_run_with_its_ordinal(world):
    w = world
    response = await w.client.post(
        f"/api/v1/release-readiness/{w.run_r}/override",
        json={"override_recommendation": "NO_GO", "reason": "blocker found"}, headers=w.lead,
    )
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"] == "NO_GO"
    rows = await _rows(w)
    assert {r.user_id for r in rows} == w.recipients
    scope = f"quality-gate-failed:run:{w.run_r}:override:1:v1"
    for row in rows:
        assert row.run_id == w.run_r
        assert row.delivery_key == _key(scope, row.user_id)
        assert row.delivery_metadata["app_path"] == f"/release-gate/{w.run_r}"
        assert row.title == f"Quality gate failed: w06a-p-{w.tag} build w06a-{w.tag} — pay is broken"

    history = await w.client.get("/api/v1/notifications/history", headers=w.member)
    [item] = [i for i in history.json() if i["event_type"] == EVENT]
    assert item["link"] == f"/release-gate/{w.run_r}"

    # Back to GO: an override out of NO_GO stages nothing more.
    back = await w.client.post(
        f"/api/v1/release-readiness/{w.run_r}/override",
        json={"override_recommendation": "GO", "reason": "fixed"}, headers=w.lead,
    )
    assert back.status_code == 200, back.text
    assert len(await _rows(w)) == 3


async def test_the_agent_path_is_withheld_by_the_review_gate_and_staged_once_otherwise(world, monkeypatch):
    from app.core.config import settings
    from app.services.notification import gate_notifications

    w = world
    monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", True)
    async with w.sessions() as db:
        outcome = await gate_notifications.stage_agent_decision_no_go(
            db, run_id=str(w.run_agent), pipeline_run_id=None, evidence_bundle_sha256=None,
        )
        await db.commit()
    assert outcome == "withheld"
    assert await _rows(w) == []

    monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", False)
    for _ in range(2):  # a re-published decision does not notify twice
        async with w.sessions() as db:
            outcome = await gate_notifications.stage_agent_decision_no_go(
                db, run_id=str(w.run_agent), pipeline_run_id=None, evidence_bundle_sha256=None,
            )
            await db.commit()
        assert outcome == "staged"
    rows = await _rows(w)
    assert {r.user_id for r in rows} == w.recipients and len(rows) == 3
    scope = f"quality-gate-failed:run:{w.run_agent}:agent:v1"
    for row in rows:
        assert row.run_id == w.run_agent and row.delivery_key == _key(scope, row.user_id)
        assert row.delivery_metadata["trigger"] == "agent"
