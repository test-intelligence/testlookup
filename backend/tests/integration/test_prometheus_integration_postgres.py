"""Real PostgreSQL proof of W23d AGT-15: the Prometheus block saved, resolved, probed and used by the log stage.

On a database migrated to head (0246), through the real routers and the app's own engine:

* an ADMIN ``PUT /api/v1/settings/integrations`` stores the bearer token in ``secret_refs`` (encrypted, never in
  ``app_settings.value``) and the templates in the row; the ``GET`` answers ``prometheus_token_set: true`` and no
  token; ``resolve_runtime_integrations`` returns the block;
* ``POST /api/v1/integration-health/probe?provider=prometheus`` and the log stage, against the shared fake
  (``tests/fakes/fake_integrations.py`` over ``httpx.ASGITransport``), answer healthy / "p95 latency for checkout
  rose 3.1× in the failure window";
* ``AI_OFFLINE_MODE=true``: the probe is ``skipped``, the stage says "Prometheus lookup disabled: offline mode",
  and the fake receives ZERO requests.

The app engine is disposed before and after; nothing here binds Mongo, and Redis is closed only if this module
bound it (checked alone and in CI order). Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``.
"""
from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import delete, select, text

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

PATH = "/api/v1/settings/integrations"
FAKE_BASE = "http://prometheus.fake.test:9090"
ROSE = "p95 latency for checkout rose 3.1× in the failure window"
CUSTOM = {"id": "checkout_errors", "label": "Checkout errors", "unit": "per_second",
          "promql": 'sum(rate(errors_total{app="{service}"}[1m]))'}


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def world(monkeypatch):
    _dsn()
    from fastapi import FastAPI

    from app.core import deps
    from app.core.config import settings
    from app.db import mongo as app_mongo, postgres as app_postgres, redis_client
    from app.models.postgres import (
        AppSetting, IntegrationHealthCheck, IntegrationProbeResult, SecretRef, SettingsAuditLog, User, UserRole,
    )
    from app.routers import app_settings, integration_health
    from app.services import app_metrics_service, integration_probe_service, url_safety
    from app.services.integration_config_service import clear_runtime_integration_cache
    from tests.fakes.fake_integrations import FakeState, create_app

    await app_postgres.dispose_engine_for_loop()
    mongo_was_bound, redis_was_bound = app_mongo._client is not None, redis_client._pool is not None
    sessions = app_postgres.AsyncSessionLocal
    tag = uuid.uuid4().hex[:12]
    token = f"prom_w23dIntegrationBearer{tag}Qx"
    admin = User(id=uuid.uuid4(), email=f"w23d-admin-{tag}@example.com", username=f"w23d-admin-{tag}",
                 hashed_password="integration", role=UserRole.ADMIN.value, is_active=True)
    lead = User(id=uuid.uuid4(), email=f"w23d-lead-{tag}@example.com", username=f"w23d-lead-{tag}",
                hashed_password="integration", role=UserRole.QA_LEAD.value, is_active=True)
    async with sessions() as db:
        prior = await db.get(AppSetting, "integrations_config")
        prior_value = dict(prior.value) if prior is not None and prior.value else None
        db.add_all([admin, lead])
        await db.commit()
    for user in (admin, lead):
        deps._bind_credential_kind(user, deps.CREDENTIAL_KIND_JWT)

    state = FakeState(prometheus_token=token)
    fake = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)))

    def getaddrinfo(host, *_a, **_k):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(integration_probe_service, "get_http_client", lambda: fake)
    monkeypatch.setattr(app_metrics_service, "get_http_client", lambda: fake)
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    clear_runtime_integration_cache()

    current = SimpleNamespace(user=admin)
    app = FastAPI()
    app.include_router(app_settings.router)
    app.include_router(integration_health.router)
    app.dependency_overrides[deps.get_current_active_user] = lambda: current.user

    async def call(method: str, path: str, **kwargs):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    try:
        yield SimpleNamespace(sessions=sessions, call=call, current=current, admin=admin, lead=lead,
                              token=token, state=state, settings=settings)
    finally:
        clear_runtime_integration_cache()
        async with sessions() as db:
            row = await db.get(AppSetting, "integrations_config")
            if prior_value is None and row is not None:
                await db.delete(row)
            elif row is not None:
                row.value = prior_value
            await db.execute(delete(SecretRef).where(SecretRef.scope == "integrations_config",
                                                     SecretRef.key_name == "prometheus_bearer_token"))
            await db.execute(delete(IntegrationHealthCheck).where(IntegrationHealthCheck.provider == "prometheus"))
            await db.execute(delete(IntegrationProbeResult).where(IntegrationProbeResult.provider == "prometheus"))
            await db.execute(delete(SettingsAuditLog).where(SettingsAuditLog.actor_id.in_([admin.id, lead.id])))
            await db.execute(delete(User).where(User.id.in_([admin.id, lead.id])))
            await db.commit()
        await fake.aclose()
        await app_postgres.dispose_engine_for_loop()
        # Close ONLY what this module bound (no Mongo path here; Redis only if a dependency bound it).
        if not redis_was_bound and redis_client._pool is not None:
            await redis_client.close_redis()
        if not mongo_was_bound and app_mongo._client is not None:
            await app_mongo.close_mongo()


async def _save(world) -> httpx.Response:
    world.current.user = world.admin
    return await world.call("PUT", PATH, json={
        "prometheus_enabled": True, "prometheus_base_url": FAKE_BASE,
        "prometheus_bearer_token": world.token, "prometheus_metric_templates": [CUSTOM],
    })


def _stage_state() -> dict:
    failed_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    return {
        "pipeline_run_id": "",
        "project_id": None,
        "analyses": {"tc-1": {"service_name": "checkout", "timestamp_utc": failed_at}},
        "failure_clusters": [],
        "test_run_data": {},
    }


def _agent():
    from app.agents.log_intelligence_agent import LogIntelligenceAgent

    agent = LogIntelligenceAgent()
    for name in ("mark_stage_running", "mark_stage_done", "log_decision"):
        setattr(agent, name, AsyncMock())
    agent.investigate = AsyncMock(return_value={
        "status": "complete", "distributed_trace": {"causal_summary": "trace"},
        "log_anomaly": {"anomaly_detected": False}, "log_summary": "Trace: trace | Anomaly: none",
    })
    return agent


async def test_an_admin_save_stores_the_token_as_a_secret_and_the_runtime_resolves_the_block(world):
    from app.models.postgres import AppSetting, SecretRef
    from app.services.integration_config_service import resolve_runtime_integrations

    response = await _save(world)
    assert response.status_code == 200, response.text
    assert world.token not in response.text

    async with world.sessions() as db:
        row = await db.get(AppSetting, "integrations_config")
        stored = (await db.execute(text(
            "SELECT value::text FROM app_settings WHERE key = 'integrations_config'"))).scalar_one()
        secret = (await db.execute(select(SecretRef).where(
            SecretRef.scope == "integrations_config", SecretRef.key_name == "prometheus_bearer_token",
        ))).scalar_one()
        resolved = await resolve_runtime_integrations(db, None)
    assert row.value["prometheus_base_url"] == FAKE_BASE
    assert row.value["prometheus_metric_templates"] == [CUSTOM]
    assert "prometheus_bearer_token" not in row.value and world.token not in stored
    assert world.token not in str(secret.encrypted_value)
    assert (resolved.prometheus_enabled, resolved.prometheus_base_url, resolved.prometheus_bearer_token) == (
        True, FAKE_BASE, world.token)
    assert [t["id"] for t in resolved.prometheus_templates] == ["checkout_errors"]
    assert world.token not in repr(resolved)

    world.current.user = world.lead
    body = (await world.call("GET", PATH)).json()
    assert body["prometheus_token_set"] is True and body["prometheus_templates_source"] == "saved"
    assert world.token not in str(body)


async def test_the_probe_and_the_log_stage_answer_against_the_fake(world):
    assert (await _save(world)).status_code == 200
    # The stage's p95 template is a default one: hand the defaults back.
    assert (await world.call("PUT", PATH, json={"prometheus_metric_templates": []})).status_code == 200

    world.current.user = world.lead
    probe = await world.call("POST", "/api/v1/integration-health/probe", params={"provider": "prometheus"})
    assert probe.status_code == 200, probe.text
    (row,) = probe.json()
    assert (row["provider"], row["status"], row["message"]) == ("prometheus", "healthy", "Prometheus API reachable")

    delta = await _agent().run(_stage_state())
    findings = delta["log_findings"]
    assert findings["status"] == "complete"
    assert findings["app_metrics"]["outcome"] == "ok"
    assert ROSE in findings["metrics_summary"]
    assert any(r["source"] == "prometheus" and r["excerpt"] == ROSE for r in findings["evidence_refs"])
    assert {q["auth"] for q in world.state.prometheus_queries} == {"ok"}
    assert world.token not in repr(delta)


async def test_offline_mode_skips_the_probe_and_the_stage_with_zero_requests(world, monkeypatch):
    assert (await _save(world)).status_code == 200
    world.state.reset()
    monkeypatch.setattr(world.settings, "AI_OFFLINE_MODE", True)

    world.current.user = world.lead
    probe = await world.call("POST", "/api/v1/integration-health/probe", params={"provider": "prometheus"})
    (row,) = probe.json()
    assert row["status"] == "skipped" and "AI_OFFLINE_MODE=true" in row["message"]

    delta = await _agent().run(_stage_state())
    metrics = delta["log_findings"]["app_metrics"]
    assert (metrics["outcome"], metrics["sentence"]) == ("offline_mode", "Prometheus lookup disabled: offline mode")
    assert world.state.prometheus_queries == []
    assert world.state.requests == []
