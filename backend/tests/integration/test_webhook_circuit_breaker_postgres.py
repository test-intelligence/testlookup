"""Real PostgreSQL proof of W10 INT-07: the circuit breaker trips exactly once, and a person can re-arm it.

On a database migrated to head (0224) the real ``_transition_processing_delivery``
records two threshold-crossing failures in two concurrent transactions; the
breaker's guarded ``UPDATE ... RETURNING`` runs in each. What only PostgreSQL
shows:

* the increment's row lock serialises the two workers: exactly ONE
  transaction gets a ``RETURNING`` row, so one activity row, one notification
  per recipient (the last editor and the project's QA leads, active, once) and
  one dashboard increment, after its commit;
* ``emit_event`` stages no delivery for the paused subscription;
* ``PATCH /api/v1/webhooks/{id}`` with ``enabled: true`` resets the count and
  clears ``disabled_reason`` / ``disabled_at``; with ``enabled: false`` it
  writes ``disabled_reason = 'manual'``;
* ``OUTBOUND_WEBHOOK_PRIVATE_ALLOWED_HOSTS``: a listed ``127.0.0.1`` receiver is
  accepted at create and reached through the shared client; an unlisted
  private host is refused with today's 422.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.fakes.fake_integrations import FakeState, create_app

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

LISTED = "http://127.0.0.1:18093"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from app.core import deps
    from app.core.config import settings
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.models.postgres import Project, ProjectMember, User, UserRole, WebhookSubscription
    from app.routers import webhooks_outbound
    from app.services import webhook_breaker
    from app.services import webhook_service as svc

    engine = create_async_engine(_dsn(), pool_size=6, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    tag = uuid.uuid4().hex[:8]
    pid = uuid.uuid4()

    def person(name, role, active=True):
        return User(id=uuid.uuid4(), email=f"{name}-{tag}@example.test", username=f"{name}-{tag}",
                    full_name=name.title(), hashed_password="x", role=role, is_active=active)

    lead, other_lead, gone = person("lee", UserRole.QA_LEAD), person("kim", UserRole.QA_LEAD), \
        person("gone", UserRole.QA_LEAD, active=False)
    engineer = person("eng", UserRole.QA_ENGINEER)
    sub_id = uuid.uuid4()
    async with sessions.begin() as db:
        db.add_all([Project(id=pid, name=f"breaker-{tag}", slug=f"breaker-{tag}", is_active=True),
                    lead, other_lead, gone, engineer])
        await db.flush()
        for user, role in ((lead, "QA_LEAD"), (other_lead, "QA_LEAD"), (gone, "QA_LEAD"),
                           (engineer, "QA_ENGINEER")):
            db.add(ProjectMember(user_id=user.id, project_id=pid, role=role))
        db.add(WebhookSubscription(id=sub_id, project_id=pid, name=f"release hook {tag}",
                                   target_url=f"{LISTED}/hooks/fail", events=["run.completed"], enabled=True,
                                   max_retries=0, breaker_threshold=3, consecutive_failures=2,
                                   updated_by_user_id=lead.id))

    fake = FakeState()
    shared = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(fake)))
    counter = SimpleNamespace(count=0)
    counter.inc = lambda amount=1: setattr(counter, "count", counter.count + amount)
    trips: list = []
    original_trip = webhook_breaker.trip_if_due

    async def recording_trip(db, subscription_id, *, now):
        trip = await original_trip(db, subscription_id, now=now)
        trips.append(trip)
        return trip

    async def accessible(_db, _user):
        return {pid}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions)
    monkeypatch.setattr(svc, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(svc, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(svc, "get_http_client", lambda: shared)
    monkeypatch.setattr(svc, "get_public_http_client",
                        MagicMock(side_effect=AssertionError("a listed host went through the public client")))
    monkeypatch.setattr(settings, "OUTBOUND_WEBHOOK_PRIVATE_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setattr("app.core.metrics.webhook_circuit_opened_total", counter)
    monkeypatch.setattr(webhook_breaker, "trip_if_due", recording_trip)
    monkeypatch.setattr(deps, "get_accessible_project_ids", accessible)
    from app.worker import tasks as worker_tasks

    monkeypatch.setattr(worker_tasks.deliver_webhook, "delay", MagicMock())

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = FastAPI()
    app.include_router(webhooks_outbound.router)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: lead

    async def call(method, path, **kwargs):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                     base_url="http://t") as client:
            return await client.request(method, f"/api/v1/webhooks{path}", **kwargs)

    async def one(sql, **params):
        async with sessions() as db:
            return (await db.execute(text(sql), params)).first()

    async def processing_delivery():
        delivery_id, token = uuid.uuid4(), uuid.uuid4()
        async with sessions.begin() as db:
            await db.execute(text(
                "INSERT INTO webhook_deliveries (id, subscription_id, event_type, event_payload, status, "
                "attempt_count, dispatch_token) VALUES (:id, :s, 'run.completed', CAST('{}' AS jsonb), "
                "'PROCESSING', 1, :t)"
            ), {"id": delivery_id, "s": sub_id, "t": token})
        return delivery_id, token

    try:
        yield SimpleNamespace(sessions=sessions, pid=pid, sub_id=sub_id, tag=tag, lead=lead,
                              other_lead=other_lead, fake=fake, counter=counter, trips=trips, svc=svc,
                              breaker=webhook_breaker, call=call, one=one,
                              processing_delivery=processing_delivery)
    finally:
        monkeypatch.undo()
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM notification_logs WHERE project_id = :p"), {"p": pid})
            await conn.execute(text("DELETE FROM project_activity_events WHERE project_id = :p"), {"p": pid})
            await conn.execute(text(
                "DELETE FROM webhook_deliveries WHERE subscription_id IN "
                "(SELECT id FROM webhook_subscriptions WHERE project_id = :p)"), {"p": pid})
            await conn.execute(text(
                "DELETE FROM settings_audit_log WHERE setting_key IN (SELECT 'webhook_subscription:' || id::text "
                "FROM webhook_subscriptions WHERE project_id = :p)"), {"p": pid})
            await conn.execute(text("DELETE FROM webhook_subscriptions WHERE project_id = :p"), {"p": pid})
            await conn.execute(text("DELETE FROM project_members WHERE project_id = :p"), {"p": pid})
            await conn.execute(text("DELETE FROM projects WHERE id = :p"), {"p": pid})
            await conn.execute(text("DELETE FROM users WHERE username LIKE :t"), {"t": f"%-{tag}"})
        await engine.dispose()


async def test_two_concurrent_threshold_failures_trip_exactly_once(world):
    first, second = await world.processing_delivery(), await world.processing_delivery()
    now = datetime.now(timezone.utc)

    async def hold_the_lock(_db):
        await asyncio.sleep(0.4)  # the second worker is now blocked on the row lock

    async def record_failure(delivery, *, before_commit=None):
        async with world.sessions() as db:
            return await world.svc._transition_processing_delivery(
                db,
                delivery_id=delivery[0],
                dispatch_token=delivery[1],
                delivery_values={"status": "DLQ", "error": "HTTP 500: receiver error", "dispatch_token": None},
                subscription_id=world.sub_id,
                subscription_values=world.breaker.terminal_failure_values("HTTP 500: receiver error", now),
                before_commit=before_commit,
                breaker="failure",
            )

    async def second_worker():
        await asyncio.sleep(0.1)
        return await record_failure(second)

    assert await asyncio.gather(record_failure(first, before_commit=hold_the_lock), second_worker()) == [True, True]

    row = await world.one(
        "SELECT enabled, disabled_reason, disabled_at, consecutive_failures, failure_count "
        "FROM webhook_subscriptions WHERE id = :s", s=world.sub_id)
    assert (row.enabled, row.disabled_reason, row.consecutive_failures, row.failure_count) == (
        False, "circuit_open", 4, 2)
    assert row.disabled_at is not None
    assert len([t for t in world.trips if t is not None]) == 1, "the guarded UPDATE returned twice"
    assert world.counter.count == 1

    activity = await world.one(
        "SELECT count(*) AS n, min(summary) AS summary FROM project_activity_events "
        "WHERE project_id = :p AND event_type = 'webhook.circuit_opened'", p=world.pid)
    assert activity.n == 1
    assert activity.summary == f"Webhook release hook {world.tag} paused after 3 consecutive failures"
    async with world.sessions() as db:
        notes = (await db.execute(text(
            "SELECT user_id, channel, event_type, run_id, title FROM notification_logs WHERE project_id = :p"
        ), {"p": world.pid})).all()
    assert sorted(n.user_id for n in notes) == sorted([world.lead.id, world.other_lead.id])
    assert {(n.channel, n.event_type, n.run_id) for n in notes} == {("email", "webhook.circuit_opened", None)}


async def test_a_paused_subscription_gets_no_delivery(world):
    staged = await world.svc.emit_event("run.completed", project_id=world.pid, payload={"run_id": None})
    assert staged == 0
    pending = await world.one(
        "SELECT count(*) AS n FROM webhook_deliveries WHERE subscription_id = :s AND status = 'PENDING'",
        s=world.sub_id)
    assert pending.n == 0


def _write(enabled: bool) -> dict:
    return {"name": "release hook", "target_url": f"{LISTED}/hooks/fail", "events": ["run.completed"],
            "enabled": enabled, "max_retries": 0}


async def test_re_enable_resets_the_count_and_a_manual_pause_says_manual(world):
    resp = await world.call("PATCH", f"/{world.sub_id}", json=_write(True))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["enabled"], body["consecutive_failures"], body["disabled_reason"], body["disabled_at"]) == (
        True, 0, None, None)
    assert body["breaker_threshold"] == 3, "a write without a threshold keeps the stored one"

    resp = await world.call("PATCH", f"/{world.sub_id}", json={**_write(False), "breaker_threshold": 5})
    assert resp.status_code == 200, resp.text
    row = await world.one(
        "SELECT enabled, disabled_reason, disabled_at, breaker_threshold FROM webhook_subscriptions "
        "WHERE id = :s", s=world.sub_id)
    assert (row.enabled, row.disabled_reason, row.breaker_threshold) == (False, "manual", 5)
    assert row.disabled_at is not None

    resp = await world.call("PATCH", f"/{world.sub_id}", json={**_write(True), "breaker_threshold": 101})
    assert resp.status_code == 422


async def test_a_listed_private_receiver_is_accepted_and_reached_an_unlisted_one_refused(world):
    resp = await world.call("POST", f"?project_id={world.pid}", json={
        "name": "local receiver", "target_url": f"{LISTED}/hooks/ok", "events": ["run.completed"],
        "max_retries": 0, "breaker_threshold": 4,
    })
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert (created["breaker_threshold"], created["consecutive_failures"]) == (4, 0)

    refused = await world.call("POST", f"?project_id={world.pid}", json={
        "name": "not listed", "target_url": "http://127.0.0.2:18093/hooks/ok", "events": ["run.completed"],
    })
    assert refused.status_code == 422
    assert refused.json()["detail"].startswith("Webhook target URL rejected: ")

    delivery_id = uuid.uuid4()
    async with world.sessions.begin() as db:
        await db.execute(text(
            "INSERT INTO webhook_deliveries (id, subscription_id, event_type, event_payload, status, attempt_count) "
            "VALUES (:id, :s, 'run.completed', CAST('{}' AS jsonb), 'PENDING', 0)"
        ), {"id": delivery_id, "s": uuid.UUID(created["id"])})
    assert await world.svc.deliver(delivery_id) == {"status": "SUCCESS", "http_status": 200}
    assert [(h["path"], h["delivery_id"]) for h in world.fake.hooks] == [("/hooks/ok", str(delivery_id))]
