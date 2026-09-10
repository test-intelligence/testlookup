"""Live events must be authorised for the project they are written to.

Re-audit finding H1. ``POST /ws/events/{run_id}`` was gated by
``verify_webhook_secret`` alone:

    @router.post("/events/{run_id}", dependencies=[Depends(verify_webhook_secret)])
    async def ingest_live_event(run_id: str, event: dict = Body(...)):

One shared, deployment-wide secret authenticated the caller but named no
tenant, and the handler took ``project_id`` straight from the request body with
no membership check. Anyone holding that secret — which
``validate_production_secrets`` rated only WARNING, so production booted with
the literal default committed in the source — could POST a ``run_start`` naming
any victim project and then stream fabricated ``test_result`` events into that
project's live dashboard and release-risk signals.

The fix prefers a project-scoped API key (the project is derived from the key,
so the body cannot address another tenant), keeps the shared secret as a legacy
path that can be switched off, compares it in constant time, and binds a run to
one project so later events cannot cross over.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.routers.live import ingest_live_event

PROJECT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PROJECT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
RUN = "run-123"


@pytest.fixture
def wired(monkeypatch):
    """Stub only the transport and the store; leave every check real."""
    published: list[tuple[str, dict]] = []

    async def _publish(run_id, event):
        published.append((run_id, event))
        return "1-0"

    monkeypatch.setattr("app.streams.producer.publish_live_event", _publish)

    class _Coll:
        async def insert_one(self, _doc):
            return None

    monkeypatch.setattr(
        "app.db.mongo.get_mongo_db", lambda: {"live_execution_events": _Coll()}
    )

    remembered: dict[str, str] = {}

    async def _remember(run_id, project_id):
        remembered.setdefault(run_id, project_id)

    async def _resolve(run_id):
        return remembered.get(run_id)

    monkeypatch.setattr(
        "app.services.live_event_authz.remember_run_project", _remember
    )
    monkeypatch.setattr(
        "app.services.live_event_authz.resolve_run_project", _resolve
    )
    monkeypatch.setattr(settings, "LIVE_EVENTS_REQUIRE_PROJECT_KEY", False)
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "the-real-secret")

    return SimpleNamespace(published=published, remembered=remembered)


def _key_ctx(project_id):
    return SimpleNamespace(
        user=SimpleNamespace(id="u"),
        project_id=project_id,
        api_key_id="k",
        api_key_name="ci",
    )


async def _call(**kwargs):
    params = {
        "run_id": RUN,
        "event": {"type": "test_result", "test_name": "t", "status": "PASSED"},
        "x_api_key": None,
        "x_webhook_secret": None,
        "db": SimpleNamespace(),
    }
    params.update(kwargs)
    return await ingest_live_event(**params)


# ── The endpoint must demand a credential at all ─────────────────────────────


@pytest.mark.asyncio
async def test_no_credential_is_rejected(wired):
    with pytest.raises(HTTPException) as exc:
        await _call()
    assert exc.value.status_code == 401
    assert wired.published == []


@pytest.mark.asyncio
async def test_a_wrong_shared_secret_is_rejected(wired):
    with pytest.raises(HTTPException) as exc:
        await _call(x_webhook_secret="not-the-secret")
    assert exc.value.status_code == 403
    assert wired.published == []


@pytest.mark.asyncio
async def test_the_legacy_shared_secret_still_works_by_default(wired):
    result = await _call(x_webhook_secret="the-real-secret")
    assert result["accepted"] is True
    assert len(wired.published) == 1


@pytest.mark.asyncio
async def test_deployments_can_refuse_the_shared_secret_outright(wired, monkeypatch):
    """The switch that closes the cross-tenant path for good."""
    monkeypatch.setattr(settings, "LIVE_EVENTS_REQUIRE_PROJECT_KEY", True)

    with pytest.raises(HTTPException) as exc:
        await _call(x_webhook_secret="the-real-secret")
    assert exc.value.status_code == 403
    assert "project-scoped API key" in exc.value.detail
    assert wired.published == []


# ── The forgery the audit reported ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_api_key_cannot_name_another_project(wired, monkeypatch):
    """A key for project A sending run_start for B must write to A.

    The body is not trusted: the project comes from the credential.
    """
    monkeypatch.setattr(
        "app.routers.live.get_streaming_api_key_context",
        AsyncMock(return_value=_key_ctx(PROJECT_A)),
    )

    await _call(
        x_api_key="k",
        event={"type": "run_start", "project_id": PROJECT_B, "build_number": "1"},
    )

    _run_id, published = wired.published[0]
    assert published["project_id"] == PROJECT_A, (
        "the caller's forged project_id reached the stream — a key for one "
        "project could open a run against another"
    )
    assert wired.remembered[RUN] == PROJECT_A


@pytest.mark.asyncio
async def test_a_later_event_cannot_cross_into_another_projects_run(
    wired, monkeypatch
):
    """Project A opens the run; project B's key may not append to it."""
    monkeypatch.setattr(
        "app.routers.live.get_streaming_api_key_context",
        AsyncMock(return_value=_key_ctx(PROJECT_A)),
    )
    await _call(
        x_api_key="ka",
        event={"type": "run_start", "project_id": PROJECT_A, "build_number": "1"},
    )

    monkeypatch.setattr(
        "app.routers.live.get_streaming_api_key_context",
        AsyncMock(return_value=_key_ctx(PROJECT_B)),
    )
    with pytest.raises(HTTPException) as exc:
        await _call(x_api_key="kb")

    assert exc.value.status_code == 403
    assert len(wired.published) == 1


@pytest.mark.asyncio
async def test_the_owning_project_may_continue_its_own_run(wired, monkeypatch):
    """The binding must not block the legitimate producer."""
    monkeypatch.setattr(
        "app.routers.live.get_streaming_api_key_context",
        AsyncMock(return_value=_key_ctx(PROJECT_A)),
    )
    await _call(
        x_api_key="ka",
        event={"type": "run_start", "project_id": PROJECT_A, "build_number": "1"},
    )
    await _call(x_api_key="ka")

    assert len(wired.published) == 2


@pytest.mark.asyncio
async def test_an_unbound_run_is_not_blocked(wired, monkeypatch):
    """No binding means "cannot verify", which must not break existing runs."""
    monkeypatch.setattr(
        "app.routers.live.get_streaming_api_key_context",
        AsyncMock(return_value=_key_ctx(PROJECT_A)),
    )
    result = await _call(x_api_key="ka")
    assert result["accepted"] is True


# ── Secret handling ──────────────────────────────────────────────────────────


def test_the_secret_is_compared_in_constant_time():
    import inspect

    from app.core import deps
    from app.routers import live

    assert "hmac.compare_digest" in inspect.getsource(deps.verify_webhook_secret)
    assert "hmac.compare_digest" in inspect.getsource(live.ingest_live_event)


def test_a_default_webhook_secret_refuses_to_boot_production(monkeypatch):
    """It gates a cross-tenant write, so it is CRITICAL, not a WARNING."""
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "change-me-webhook-secret")
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "a-real-jwt-secret")
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "a-real-app-secret")
    monkeypatch.setattr(settings, "DEV_AUTO_LOGIN_ENABLED", False)

    critical = settings.critical_security_failures()
    assert any("WEBHOOK_SECRET" in item for item in critical), (
        "a default WEBHOOK_SECRET no longer blocks production startup, so a "
        "deployment can ship with the secret published in the source tree"
    )
