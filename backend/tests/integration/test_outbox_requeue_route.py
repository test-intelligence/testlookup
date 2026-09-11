"""The outbox requeue is an instance admin's tool, and a dry run by default (re-audit N23).

The query itself is tested against real PostgreSQL in
``test_outbox_requeue_postgres.py``; these pin who may call the route and what
it passes on.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jose")

from app.models.postgres import UserRole  # noqa: E402

pytestmark = pytest.mark.asyncio

URL = "/api/v1/admin/maintenance/outbox/requeue"
ROW = {
    "id": "row-1",
    "run_id": "run-1",
    "project_id": "project-1",
    "last_error": "broker_TypeError",
    "dispatch_failures": 8,
    "execution_attempts": 0,
    "created_at": None,
}


@pytest.fixture
def requeue(monkeypatch):
    calls: list[dict] = []

    async def _fake(db, **kwargs):
        calls.append(kwargs)
        if kwargs["operation"] != "agent_pipeline":
            raise ValueError(f"unknown downstream operation: {kwargs['operation']!r}")
        return [dict(ROW)]

    monkeypatch.setattr(
        "app.services.run_downstream_outbox.requeue_failed_downstream_operations", _fake
    )
    return calls


async def test_it_is_a_dry_run_unless_told_otherwise(client, auth_as, requeue):
    auth_as(role=UserRole.ADMIN)
    resp = await client.post(URL, json={"operation": "agent_pipeline", "last_error": "broker_TypeError"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["dry_run"], body["matched"], body["requeued"]) == (True, 1, 0)
    assert body["rows"] == [ROW]
    assert requeue[-1]["dry_run"] is True


async def test_an_instance_admin_can_requeue(client, auth_as, requeue):
    auth_as(role=UserRole.ADMIN)
    run_id = uuid.uuid4()
    resp = await client.post(
        URL, json={
            "operation": "agent_pipeline", "last_error": "broker_TypeError",
            "run_id": str(run_id), "limit": 50, "dry_run": False,
        }
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["requeued"] == 1
    assert requeue[-1] == {
        "operation": "agent_pipeline",
        "last_error": "broker_TypeError",
        "run_id": run_id,
        "limit": 50,
        "dry_run": False,
    }


@pytest.mark.parametrize("role", [UserRole.QA_LEAD, UserRole.QA_ENGINEER, UserRole.VIEWER])
async def test_only_an_instance_admin_can_requeue(client, auth_as, requeue, role):
    auth_as(role=role)
    assert (await client.post(URL, json={"operation": "agent_pipeline"})).status_code == 403
    assert requeue == []


async def test_an_admins_project_bound_key_cannot_requeue(client, auth_as, requeue):
    """The outbox spans every project; a key bound to one must not reach it."""
    auth_as(role=UserRole.ADMIN, bound_project_id=uuid.uuid4())
    resp = await client.post(URL, json={"operation": "agent_pipeline", "dry_run": False})
    assert resp.status_code == 403
    assert requeue == []


async def test_an_unknown_operation_is_a_422(client, auth_as, requeue):
    auth_as(role=UserRole.ADMIN)
    resp = await client.post(URL, json={"operation": "drop_tables", "last_error": "broker_TypeError"})
    assert resp.status_code == 422
    assert "unknown downstream operation" in resp.text


async def test_a_requeue_must_name_the_failure_it_recovers_from(client, auth_as, requeue):
    """Without it one call re-ran up to 500 intents that had failed for any
    reason, notifications and webhooks included (code review round 3)."""
    auth_as(role=UserRole.ADMIN)
    for body in ({"operation": "agent_pipeline"}, {"operation": "agent_pipeline", "last_error": ""}):
        assert (await client.post(URL, json={**body, "dry_run": False})).status_code == 422
    assert requeue == []


async def test_a_limit_above_the_cap_is_refused_before_anything_runs(client, auth_as, requeue):
    auth_as(role=UserRole.ADMIN)
    assert (await client.post(URL, json={"operation": "agent_pipeline", "limit": 501})).status_code == 422
    assert requeue == []
