"""W09 NTF-02 on real PostgreSQL: the transition-policy PUT and its ledger row, through the app.

* a QA lead's PUT writes the policy row and one ``notification_policy.updated``
  row in ONE transaction: the project's activity feed lists
  ``Notification policy changed: every run`` with the link to the card, and
  the GET answers the stored values (``is_default: false``);
* a forced failure after the ledger stage (before the commit) leaves neither
  row;
* a first save of the defaults records the defaults sentence; the same values
  again record nothing more;
* a QA engineer, and a QA lead of another project, are refused 403 and write
  nothing.

Uses ``tests/integration/defect_w06_world.py`` (projects P and Q, the app over
ASGI). Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (0221) and
``REDIS_URL``.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from tests.integration.defect_w06_world import defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

EVENTS = [
    "test.newly_failing", "test.recovered", "test.newly_flaky", "test.quarantined",
    "test.unquarantined", "test.quarantine_stale", "test.ready_to_unquarantine",
]
DEFAULTS = {"transitions_enabled": True, "per_run_events_enabled": False, "enabled_events": EVENTS,
            "consecutive_failure_threshold": 2}


@pytest.fixture
async def world():
    from app.models.postgres import ProjectMember, UserRole

    async with defect_world() as w:
        async with w.sessions.begin() as db:
            w.lead_id = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Priya Nair")
            w.lead_q_id = await w.user(db, role=UserRole.QA_LEAD.value)
            db.add(ProjectMember(project_id=w.P, user_id=w.lead_id, role=UserRole.QA_LEAD.value))
            db.add(ProjectMember(project_id=w.Q, user_id=w.lead_q_id, role=UserRole.QA_LEAD.value))
        w.lead, w.lead_q = w.jwt(w.lead_id), w.jwt(w.lead_q_id)
        yield w


def _url(pid) -> str:
    return f"/api/v1/notifications/projects/{pid}/transition-policy"


async def _policy_rows(w, pid):
    from app.models.postgres import NotificationTransitionPolicy

    async with w.sessions() as db:
        return list((await db.execute(
            select(NotificationTransitionPolicy).where(NotificationTransitionPolicy.project_id == pid)
        )).scalars().all())


async def _ledger(w, pid):
    from app.models.postgres import ProjectActivityEvent

    async with w.sessions() as db:
        return list((await db.execute(
            select(ProjectActivityEvent)
            .where(ProjectActivityEvent.project_id == pid,
                   ProjectActivityEvent.event_type == "notification_policy.updated")
            .order_by(ProjectActivityEvent.recorded_at)
        )).scalars().all())


async def test_a_put_writes_the_policy_and_its_ledger_row_together(world):
    w = world
    assert (await w.client.get(_url(w.P), headers=w.lead)).json()["is_default"] is True

    saved = await w.client.put(_url(w.P), json={**DEFAULTS, "per_run_events_enabled": True}, headers=w.lead)
    assert saved.status_code == 200, saved.text
    assert saved.json()["per_run_events_enabled"] is True and saved.json()["is_default"] is False

    [row] = await _policy_rows(w, w.P)
    assert row.per_run_events_enabled is True
    [entry] = await _ledger(w, w.P)
    assert entry.category == "configuration" and entry.entity_type == "notification_policy"
    assert entry.entity_id == str(w.P) and entry.actor_type == "user"
    assert entry.summary == "Notification policy changed: every run"
    assert entry.diff["before"] == {"per_run_events_enabled": False}
    assert entry.diff["after"] == {"per_run_events_enabled": True}

    feed = await w.client.get(f"/api/v1/projects/{w.P}/activity", headers=w.lead)
    assert feed.status_code == 200, feed.text
    [item] = [i for i in feed.json()["items"] if i["event_type"] == "notification_policy.updated"]
    assert item["summary"] == "Notification policy changed: every run"
    assert item["entity"]["href"] == "/settings/notifications"

    got = (await w.client.get(_url(w.P), headers=w.lead)).json()
    assert (got["per_run_events_enabled"], got["is_default"]) == (True, False)


async def test_a_failure_after_the_ledger_stage_leaves_neither_row(world, monkeypatch):
    from app.routers import notifications as route_module

    w = world
    real = route_module.record_activity

    async def record_then_fail(db, **kwargs):
        await real(db, **kwargs)
        await db.flush()  # both rows are now in the transaction
        raise RuntimeError("forced failure after the ledger stage")

    monkeypatch.setattr(route_module, "record_activity", record_then_fail)
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                           base_url="http://testserver") as client:
        failed = await client.put(_url(w.P), json={**DEFAULTS, "per_run_events_enabled": True}, headers=w.lead)
    assert failed.status_code == 500
    assert await _policy_rows(w, w.P) == []
    assert await _ledger(w, w.P) == []


async def test_the_defaults_saved_once_then_nothing_more(world):
    w = world
    first = await w.client.put(_url(w.P), json=DEFAULTS, headers=w.lead)
    assert first.status_code == 200, first.text
    again = await w.client.put(_url(w.P), json=DEFAULTS, headers=w.lead)
    assert again.status_code == 200, again.text
    assert len(await _policy_rows(w, w.P)) == 1
    [entry] = await _ledger(w, w.P)
    assert entry.summary == "Notification policy changed: saved the defaults as this project's policy"
    assert (await w.client.get(_url(w.P), headers=w.lead)).json()["is_default"] is False


async def test_a_qa_engineer_and_another_projects_lead_are_refused(world):
    w = world
    body = {**DEFAULTS, "per_run_events_enabled": True}
    engineer = await w.client.put(_url(w.P), json=body, headers=w.member)
    assert engineer.status_code == 403, engineer.text
    foreign = await w.client.put(_url(w.P), json=body, headers=w.lead_q)
    assert foreign.status_code == 403, foreign.text
    assert await _policy_rows(w, w.P) == []
    assert await _ledger(w, w.P) == []
