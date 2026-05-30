"""
API integration tests for /api/v1/webhooks — Tier 2 item 6.

Covers role gates, tenant isolation, event catalog shape, the create
happy path, invalid target URL, and the test-fire endpoint.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jose")
pytest.importorskip("asyncpg")

from app.models.postgres import UserRole  # noqa: E402

pytestmark = pytest.mark.asyncio


def _fake_sub(project_id: uuid.UUID) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        name="PagerDuty regressions",
        target_url="https://hooks.example.com/pagerduty",
        events=["run.completed", "flaky.quarantined"],
        enabled=True,
        has_secret=True,
        max_retries=5,
        last_delivered_at=None,
        last_failure_at=None,
        last_error=None,
        failure_count=0,
        total_delivered=0,
        created_at=now,
        updated_at=now,
        updated_by_user_id=None,
    )


# ── GET /webhooks/events ───────────────────────────────────────────────────


async def test_events_catalog_anyone_authenticated(client, auth_as):
    auth_as(role=UserRole.VIEWER)
    resp = await client.get("/api/v1/webhooks/events")
    assert resp.status_code == 200
    body = resp.json()
    types = {e["event_type"] for e in body["events"]}
    assert {
        "run.completed",
        "defect.promoted",
        "release.decided",
        "flaky.quarantined",
        "quota.exceeded",
    }.issubset(types)


# ── GET /webhooks (list) ───────────────────────────────────────────────────


async def test_list_webhooks_scopes_to_accessible_projects(
    client, auth_as, override_db, fake_db,
):
    auth_as(accessible_projects={uuid.uuid4()})
    with patch(
        "app.services.webhook_service.list_subscriptions",
        AsyncMock(return_value=[]),
    ):
        resp = await client.get("/api/v1/webhooks")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_with_project_id_requires_membership(
    client, auth_as, override_db, fake_db,
):
    auth_as(accessible_projects={uuid.uuid4()})
    resp = await client.get(f"/api/v1/webhooks?project_id={uuid.uuid4()}")
    assert resp.status_code == 403


# ── POST /webhooks (create) ────────────────────────────────────────────────


async def test_create_requires_qa_lead(client, auth_as, override_db, fake_db):
    auth_as(role=UserRole.QA_ENGINEER)
    project_id = uuid.uuid4()
    resp = await client.post(
        f"/api/v1/webhooks?project_id={project_id}",
        json={
            "name": "x",
            "target_url": "https://example.com/hooks",
            "events": ["run.completed"],
            "enabled": True,
            "max_retries": 5,
        },
    )
    assert resp.status_code == 403


async def test_create_rejects_non_http_target_url(
    client, auth_as, override_db, fake_db,
):
    auth_as(role=UserRole.ADMIN)
    project_id = uuid.uuid4()
    resp = await client.post(
        f"/api/v1/webhooks?project_id={project_id}",
        json={
            "name": "bad",
            "target_url": "ftp://example.com/hooks",
            "events": ["run.completed"],
            "enabled": True,
            "max_retries": 5,
        },
    )
    assert resp.status_code == 422


async def test_create_happy_path(client, auth_as, override_db, fake_db):
    project_id = uuid.uuid4()
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.webhook_service.create_subscription",
        AsyncMock(return_value=_fake_sub(project_id)),
    ) as mock_create:
        resp = await client.post(
            f"/api/v1/webhooks?project_id={project_id}",
            json={
                "name": "PagerDuty regressions",
                "target_url": "https://hooks.example.com/pagerduty",
                "events": ["run.completed", "flaky.quarantined"],
                "enabled": True,
                "max_retries": 5,
                "secret": "super-secret",
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "PagerDuty regressions"
    assert "run.completed" in body["events"]
    mock_create.assert_awaited_once()


# ── POST /webhooks/{id}/test ───────────────────────────────────────────────


async def test_test_fire_requires_qa_lead(client, auth_as, override_db, fake_db):
    auth_as(role=UserRole.QA_ENGINEER)
    resp = await client.post(f"/api/v1/webhooks/{uuid.uuid4()}/test")
    assert resp.status_code == 403


async def test_test_fire_enqueues_synthetic_event(
    client, auth_as, override_db, fake_db,
):
    project_id = uuid.uuid4()
    sub = _fake_sub(project_id)
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.webhook_service.get_subscription",
        AsyncMock(return_value=sub),
    ), patch(
        "app.services.webhook_service.emit_event",
        AsyncMock(return_value=1),
    ) as mock_emit:
        resp = await client.post(f"/api/v1/webhooks/{sub.id}/test")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    mock_emit.assert_awaited_once()
    # emit_event should be called with "run.completed" as the synthetic event.
    call = mock_emit.await_args
    assert call.args[0] == "run.completed"


async def test_test_fire_reports_zero_matches_as_failure(
    client, auth_as, override_db, fake_db,
):
    """When emit_event returns 0 (flag off, offline mode, etc.) the
    response signals failure with a helpful message."""
    project_id = uuid.uuid4()
    sub = _fake_sub(project_id)
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.webhook_service.get_subscription",
        AsyncMock(return_value=sub),
    ), patch(
        "app.services.webhook_service.emit_event",
        AsyncMock(return_value=0),
    ):
        resp = await client.post(f"/api/v1/webhooks/{sub.id}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "outbound_webhooks" in body["message"]
