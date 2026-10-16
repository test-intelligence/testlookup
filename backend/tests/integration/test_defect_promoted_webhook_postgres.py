"""Real PostgreSQL proof of DEF-07: ``defect.promoted`` stages exactly one delivery per subscription per defect.

The review-approve and the promote routes run for real on a database migrated
to head; ``emit_event`` is the real one, staging ``webhook_deliveries`` rows with
``delivery_key = sha256(scope:subscription:event)`` and ``ON CONFLICT DO
NOTHING``. Outbound webhooks are switched on by patching the gate
(``_post_allowed``) only: the flag and offline mode stay as installed. The
Celery publish is stubbed (no broker). What only Postgres shows: the delivery
row exists once, with the defect's id in its payload, however often the
emitter re-runs; the ledger row is committed with the promotion.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``; skips otherwise.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.models.postgres import (
        FailureCluster, Project, ProjectMember, TestRun, User, UserRole, WebhookSubscription,
    )
    from app.routers import deep_investigation
    from app.services import defect_promoted_webhook as hook
    from app.services import defect_promotion_service as promotion
    from app.services import webhook_service

    engine = create_async_engine(_dsn(), pool_size=5, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:8]
    pid, run_id, sub_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    lead = User(id=uuid.uuid4(), email=f"wh-{tag}@example.test", username=f"wh-{tag}", full_name="Lee Lead",
                hashed_password="x", role=UserRole.QA_LEAD, is_active=True)
    async with sessions.begin() as db:
        db.add_all([Project(id=pid, name=f"webhook-{tag}", slug=f"webhook-{tag}", is_active=True), lead])
        await db.flush()
        db.add(ProjectMember(user_id=lead.id, project_id=pid, role="QA_LEAD"))
        db.add(TestRun(id=run_id, project_id=pid, build_number=f"b-{tag}", branch="main", status="FAILED",
                       total_tests=1, failed_tests=1, created_at=now - timedelta(hours=1),
                       start_time=now - timedelta(hours=1)))
        db.add(WebhookSubscription(id=sub_id, project_id=pid, name="receiver",
                                   target_url="https://hooks.example.test/defects", events=["defect.promoted"],
                                   enabled=True))
        await db.flush()
        db.add(FailureCluster(test_run_id=run_id, cluster_id="cl_wh", label=f"Checkout 500 {tag}", size=1,
                              member_test_ids=[], representative_error="500"))

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions)
    monkeypatch.setattr(webhook_service, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(hook, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(webhook_service, "_post_allowed", AsyncMock(return_value=True))
    from app.worker import tasks as worker_tasks

    monkeypatch.setattr(worker_tasks.deliver_webhook, "delay", MagicMock())
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    monkeypatch.setattr(promotion, "_resolve_defect_owner_from_memory", AsyncMock(return_value=None))
    monkeypatch.setattr(promotion, "_find_duplicate_semantic", AsyncMock(return_value=(None, False)))

    class _Redis:
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

    async def deliveries(defect_id):
        async with sessions() as db:
            rows = await db.execute(text(
                "SELECT event_type, subscription_id, event_payload FROM webhook_deliveries "
                "WHERE subscription_id = :s AND event_payload ->> 'defect_id' = :d"),
                {"s": sub_id, "d": str(defect_id)})
            return [dict(r._mapping) for r in rows]

    try:
        yield SimpleNamespace(sessions=sessions, pid=pid, run_id=run_id, sub_id=sub_id, call=call, hook=hook,
                              promotion=promotion, deliveries=deliveries, monkeypatch=monkeypatch)
    finally:
        monkeypatch.undo()
        async with engine.begin() as db:
            await db.execute(text("DELETE FROM project_activity_events WHERE project_id = :p"), {"p": pid})
            await db.execute(text("DELETE FROM defects WHERE project_id = :p"), {"p": pid})
            await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": pid})
            await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": lead.id})
        await engine.dispose()


async def _pending(world):
    from app.models.postgres import Defect

    row = Defect(id=uuid.uuid4(), project_id=world.pid, cluster_id="cl_wh", title="Checkout 500",
                 severity="CRITICAL", resolution_status="OPEN", approval_status="pending_review",
                 policy_evaluation={"policy_reasons": ["Severity CRITICAL requires human review"],
                                    "source_run_id": str(world.run_id)})
    async with world.sessions.begin() as db:
        db.add(row)
    return row.id


async def test_approving_stages_exactly_one_delivery_however_often_it_is_emitted(world):
    defect_id = await _pending(world)

    response = await world.call("POST", f"/defects/{defect_id}/review", json={"action": "approve"})

    assert response.status_code == 200, response.text
    (delivery,) = await world.deliveries(defect_id)
    assert delivery["event_type"] == "defect.promoted"
    payload = delivery["event_payload"]
    assert payload["defect_id"] == str(defect_id) and payload["project_id"] == str(world.pid)
    assert payload["run_id"] == str(world.run_id) and payload["approval_status"] == "approved"
    assert payload["deep_link"].endswith(f"/defects?defect={defect_id}")

    # A retried approve / a re-run emitter: the delivery_key conflict keeps one row.
    assert await world.hook.emit_defect_promoted(defect_id) == 0
    assert len(await world.deliveries(defect_id)) == 1


async def test_a_pending_or_rejected_defect_is_never_delivered(world):
    pending = await _pending(world)
    assert await world.hook.emit_defect_promoted(pending) == 0
    rejected = await _pending(world)
    response = await world.call("POST", f"/defects/{rejected}/review", json={"action": "reject", "reason": "dup"})
    assert response.status_code == 200

    assert await world.deliveries(pending) == []
    assert await world.deliveries(rejected) == []


async def test_an_auto_approved_promotion_is_delivered_and_ledgered(world):
    world.monkeypatch.setattr(world.promotion, "check_defect_promotion_policy", AsyncMock(return_value={
        "initial_status": "approved", "requires_approval": False, "policy_reasons": [],
    }))

    response = await world.call("POST", f"/{world.run_id}/clusters/cl_wh/promote",
                                json={"title": "Checkout 500 auto", "severity": "MEDIUM"})

    assert response.status_code == 200, response.text
    defect_id = response.json()["defect_id"]
    (delivery,) = await world.deliveries(defect_id)
    assert delivery["event_payload"]["cluster_id"] == "cl_wh"
    async with world.sessions() as db:
        events = (await db.execute(text(
            "SELECT event_type FROM project_activity_events WHERE entity_id = :e"), {"e": defect_id})).scalars().all()
        stored = (await db.execute(text("SELECT policy_evaluation FROM defects WHERE id = :i"),
                                   {"i": defect_id})).scalar_one()
    assert events == ["defect.promoted"]
    assert stored["source_run_id"] == str(world.run_id) and stored["jira_project_key"] is None
