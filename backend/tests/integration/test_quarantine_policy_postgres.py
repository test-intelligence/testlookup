"""W09 FLK-02 on real PostgreSQL: the quarantine policy, end to end through the app.

* a QA lead of P sets SLA 7 and auto-release: the policy row and ONE
  ``quarantine_policy.updated`` ledger row commit together, the feed lists
  "Quarantine policy changed: SLA days, auto-release", and the GET reads the
  values back (``is_default: false``, ``max_active_quarantined`` included);
* a failure after the ledger stage leaves neither row (one transaction);
* the SLA reaches the next approval: a proposal approved after the PUT gets
  ``stale_at = approved_at + 7 days``;
* a QA lead of another project is refused on the path id and writes nothing.

Uses ``tests/integration/defect_w06_world.py`` (P and Q, the app over ASGI).
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head and ``REDIS_URL``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from tests.integration.defect_w06_world import defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

BODY = {
    "sla_days": 7,
    "auto_create_defect": False,
    "auto_promote": True,
    "promote_after_passes": 20,
    "detection_flip_rate_threshold": 0.2,
    "detection_min_runs": 10,
    "max_active_quarantined": 8,
}
DEFAULTS = {**BODY, "sla_days": 14, "auto_promote": False}


@pytest.fixture
async def world():
    from app.models.postgres import ProjectMember, UserRole

    async with defect_world() as w:
        async with w.sessions.begin() as db:
            w.lead_id = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Priya Nair")
            w.lead_q_id = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Quentin Other")
            db.add(ProjectMember(project_id=w.P, user_id=w.lead_id, role=UserRole.QA_LEAD.value))
            db.add(ProjectMember(project_id=w.Q, user_id=w.lead_q_id, role=UserRole.QA_LEAD.value))
        w.lead, w.lead_q = w.jwt(w.lead_id), w.jwt(w.lead_q_id)
        yield w


def _url(pid) -> str:
    return f"/api/v1/projects/{pid}/quarantine/policy"


async def _policy_rows(w, pid):
    from app.models.postgres import QuarantineLifecyclePolicy

    async with w.sessions() as db:
        return list((await db.execute(
            select(QuarantineLifecyclePolicy).where(QuarantineLifecyclePolicy.project_id == pid)
        )).scalars().all())


async def _ledger(w, pid):
    from app.models.postgres import ProjectActivityEvent

    async with w.sessions() as db:
        return list((await db.execute(
            select(ProjectActivityEvent).where(
                ProjectActivityEvent.project_id == pid,
                ProjectActivityEvent.event_type == "quarantine_policy.updated",
            )
        )).scalars().all())


async def test_a_qa_lead_changes_the_policy_and_the_ledger_row_commits_with_it(world):
    w = world
    fresh = await w.client.get(_url(w.P), headers=w.member)
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["is_default"] is True and fresh.json()["max_active_quarantined"] == 8

    assert (await w.client.put(_url(w.P), json=DEFAULTS, headers=w.lead)).status_code == 200
    [first] = await _ledger(w, w.P)
    assert first.summary == "Quarantine policy changed: saved the defaults as this project's policy"

    saved = await w.client.put(_url(w.P), json=BODY, headers=w.lead)
    assert saved.status_code == 200, saved.text
    [row] = await _policy_rows(w, w.P)
    assert (row.sla_days, row.auto_promote, row.max_active_quarantined) == (7, True, 8)
    events = await _ledger(w, w.P)
    assert len(events) == 2
    change = next(e for e in events if e.id != first.id)
    assert change.summary == "Quarantine policy changed: SLA days, auto-release"
    assert change.actor_type == "user" and change.actor_id == w.lead_id
    assert change.diff["before"] == {"sla_days": 14, "auto_promote": False}
    assert change.diff["after"] == {"sla_days": 7, "auto_promote": True}

    again = await w.client.put(_url(w.P), json=BODY, headers=w.lead)
    assert again.status_code == 200
    assert len(await _ledger(w, w.P)) == 2, "the same values again record nothing"

    feed = await w.client.get(f"/api/v1/projects/{w.P}/activity", headers=w.member)
    assert feed.status_code == 200, feed.text
    summaries = [item["summary"] for item in feed.json()["items"]]
    assert "Quarantine policy changed: SLA days, auto-release" in summaries

    read = (await w.client.get(_url(w.P), headers=w.member)).json()
    assert {k: read[k] for k in BODY} == BODY and read["is_default"] is False


async def test_a_failure_after_the_ledger_stage_leaves_neither_row(world, monkeypatch):
    from app.routers import flaky_quarantine as route_module

    real = route_module.record_activity

    async def stage_then_fail(db, **kwargs):
        await real(db, **kwargs)
        raise RuntimeError("forced failure after the ledger stage")

    monkeypatch.setattr(route_module, "record_activity", stage_then_fail)
    w = world
    with pytest.raises(RuntimeError):
        await w.client.put(_url(w.P), json=BODY, headers=w.lead)
    assert await _policy_rows(w, w.P) == []
    assert await _ledger(w, w.P) == []


async def test_the_sla_reaches_the_next_approval(world):
    from app.models.postgres import FlakyQuarantineRequest

    w = world
    assert (await w.client.put(_url(w.P), json=BODY, headers=w.lead)).status_code == 200
    request_id = uuid.uuid4()
    async with w.sessions.begin() as db:
        db.add(FlakyQuarantineRequest(
            id=request_id, project_id=w.P, test_fingerprint=f"w09c-{w.tag}", test_name="test_checkout_total",
            suite_name="checkout-api", status="PROPOSED", detection_method="manual", flip_rate=0.4,
            flip_window_size=10, pass_count=6, fail_count=4,
        ))
    approved = await w.client.post(f"/api/v1/quarantine/{request_id}/approve", json={"notes": "flaky"},
                                   headers=w.lead)
    assert approved.status_code == 200, approved.text
    body = approved.json()
    approved_at = datetime.fromisoformat(body["approved_at"])
    stale_at = datetime.fromisoformat(body["stale_at"])
    assert stale_at - approved_at == timedelta(days=7)
    assert body["sla_days"] == 7


async def test_a_qa_lead_of_another_project_writes_nothing(world):
    w = world
    refused = await w.client.put(_url(w.P), json=BODY, headers=w.lead_q)
    assert refused.status_code == 403, refused.text
    assert await _policy_rows(w, w.P) == []
    assert await _ledger(w, w.P) == []
    engineer = await w.client.put(_url(w.P), json=BODY, headers=w.member)
    assert engineer.status_code == 403, engineer.text
    assert await _policy_rows(w, w.P) == []
