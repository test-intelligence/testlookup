"""The Trends email routes never hold their connection while the relay is resolved (W08 TRND-01).

``email_service.get_smtp_config()`` reads the relay saved in Settings ›
Notifications in a session of its OWN (``AsyncSessionLocal()``). A request
that still held its session's connection then needed two at once -- on a
worker's 2 + 1 pool, a few such requests together wait out the pool timeout
(the W06 finding, ``test_no_second_session_while_the_request_holds_one.py``).

Both routes hand the request's connection back first
(``release_read_connection``). Every test runs on a pool of ONE connection
with a 1 s checkout. The stored relay is ``relay.lan``; the environment's is
different (and disabled for the availability check), so a stored-config read
that never got a connection -- ``_get_smtp_cfg`` swallows the timeout and
falls back to the environment -- shows up as the wrong relay, not as a hang.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.db.postgres as pg
from app.core.config import settings
from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models import postgres as models

PROJECT = uuid.uuid4()
MEMBER = SimpleNamespace(id=uuid.uuid4(), email="ana@corp.example")
STORED = {"enabled": True, "host": "relay.lan", "port": 25, "tls": False, "from_address": "qa@corp.example"}


class _Pool:
    def __init__(self) -> None:
        self._slot = asyncio.Semaphore(1)
        self.held = 0
        self.max_held = 0

    async def checkout(self) -> None:
        await asyncio.wait_for(self._slot.acquire(), timeout=1.0)
        self.held += 1
        self.max_held = max(self.max_held, self.held)

    def checkin(self) -> None:
        self.held -= 1
        self._slot.release()


class _Result:
    def __init__(self, sql: str) -> None:
        self._sql = sql

    def scalar_one_or_none(self):
        # The stored relay row (``app_settings.smtp_config``).
        return SimpleNamespace(value=dict(STORED)) if "app_settings" in self._sql else None

    def all(self):
        # The recipient lookup (``project_members`` JOIN ``users``).
        return [MEMBER] if "project_members" in self._sql else []


class _Session:
    """Takes the pool's connection on its first query, gives it back on close,
    and opens again on the next query, like an AsyncSession."""

    def __init__(self, pool: _Pool) -> None:
        self._pool = pool
        self._connected = False
        self.new, self.dirty, self.deleted = [], [], []

    async def execute(self, stmt, *args, **kwargs):
        if not self._connected:
            await self._pool.checkout()
            self._connected = True
        return _Result(str(stmt))

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        if self._connected:
            self._connected = False
            self._pool.checkin()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()


async def _query(db) -> None:
    await db.execute(select(models.TestRun.id))


@pytest.fixture
def world(monkeypatch):
    from app.routers import reports as reports_router
    from app.services import trends_report_service
    from app.services.analytics_scope import AnalyticsScope
    from app.services.notification import email_service

    pool = _Pool()
    sent: list = []
    # get_smtp_config opens its session from here, on the same pool.
    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _Session(pool), raising=False)
    monkeypatch.setattr("app.services.secret_service.read_secret", AsyncMock(return_value=None))
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(settings, "SMTP_ENABLED", False)
    monkeypatch.setattr(settings, "SMTP_HOST", "env-relay.invalid")

    async def scope_check(db, user, project_id):
        await _query(db)  # the membership read: the request now holds the connection
        return PROJECT, None

    async def analytics_scope(db, user, **kwargs):
        await _query(db)  # the release / suite authorisation
        return AnalyticsScope(project_id=PROJECT, allowed_project_ids=None, release_ids=(),
                              suite_names=(), days=14)

    async def build(db, scope):
        await _query(db)  # the page's reads
        from app.services.trend_summary_service import summarize, utc_today

        return {"project_name": "Phoenix", "window_days": 14, "generated_at": "2026-09-18T00:00:00+00:00",
                "trend": [], "suites": [], "summary": summarize([], [], days=14, today=utc_today()),
                "pass_rate_delta_pp": None, "meta": None}

    async def record(db, **kwargs):
        await _query(db)  # the ledger row: the request session reopens after the send

    async def send(message, **kwargs):
        sent.append(kwargs["hostname"])

    monkeypatch.setattr(reports_router, "resolve_project_scope", scope_check)
    monkeypatch.setattr(reports_router, "resolve_analytics_scope", analytics_scope)
    monkeypatch.setattr(trends_report_service, "build_trends_report", build)
    monkeypatch.setattr(reports_router, "record_activity", record)
    monkeypatch.setattr(email_service.aiosmtplib, "send", send)

    app = FastAPI()
    app.include_router(reports_router.router)

    async def _get_db():
        session = _Session(pool)
        try:
            yield session
        finally:
            await session.close()

    lead = SimpleNamespace(id=uuid.uuid4(), username="lead", full_name="Lead", email="lead@corp.example",
                           role="QA_LEAD", is_active=True, is_service_account=False)

    async def _current_user(db=Depends(get_db)):
        await _query(db)  # as the real get_current_user: the user row, on the request session
        return lead

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = _current_user
    return SimpleNamespace(app=app, pool=pool, sent=sent)


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


@pytest.mark.asyncio
async def test_the_email_post_reads_the_stored_relay_on_a_pool_of_one(world):
    async with await _client(world.app) as client:
        response = await asyncio.wait_for(client.post("/api/v1/reports/email-trends", json={
            "project_id": str(PROJECT), "days": 14, "recipient_user_ids": [str(MEMBER.id)],
        }), timeout=10)
    assert response.status_code == 200, response.text
    assert world.sent == ["relay.lan"], "the stored relay was never read: its session got no connection"
    assert world.pool.max_held == 1


@pytest.mark.asyncio
async def test_availability_reads_the_stored_relay_on_a_pool_of_one(world):
    async with await _client(world.app) as client:
        response = await asyncio.wait_for(client.get("/api/v1/reports/email-trends/availability"), timeout=10)
    assert response.status_code == 200, response.text
    # The environment's relay is disabled: only the stored one answers "available".
    assert response.json() == {"available": True, "code": None, "reason": None}
    assert world.pool.max_held == 1
