"""
API integration tests for /api/v1/feature-flags — Tier 0A.

Exercises the ADMIN CRUD endpoints plus the per-caller ``/status`` endpoint
that non-admin users can call. All DB access goes through the standard
integration fixtures so no real PostgreSQL is needed.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jose")
pytest.importorskip("asyncpg")

from app.models.postgres import UserRole  # noqa: E402
from tests.integration.conftest import fake_execute_result  # noqa: E402

pytestmark = pytest.mark.asyncio


# ── GET /api/v1/feature-flags ───────────────────────────────────────────────


async def test_list_feature_flags_requires_admin(client, auth_as):
    """Non-admin caller is rejected by the router-level require_role dep."""
    auth_as(role=UserRole.QA_LEAD)  # QA_LEAD < ADMIN
    resp = await client.get("/api/v1/feature-flags")
    assert resp.status_code == 403


async def test_list_feature_flags_ok_for_admin(client, auth_as, override_db, fake_db):
    """Admin sees the list returned by the service."""
    auth_as(role=UserRole.ADMIN)
    fake_flag = SimpleNamespace(
        id=uuid.uuid4(),
        key="cypress_ingest",
        description="Cypress parser flag",
        enabled_global=True,
        enabled_projects=None,
        enabled_roles=None,
        rollout_percent=100,
        created_at=None,
        updated_at=None,
        updated_by_user_id=None,
    )
    with patch(
        "app.services.feature_flags.list_flags",
        AsyncMock(return_value=[fake_flag]),
    ):
        resp = await client.get("/api/v1/feature-flags")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["key"] == "cypress_ingest"


# ── POST /api/v1/feature-flags ──────────────────────────────────────────────


async def test_create_feature_flag_requires_admin(client, auth_as):
    auth_as(role=UserRole.QA_LEAD)
    resp = await client.post(
        "/api/v1/feature-flags",
        json={"key": "x", "enabled_global": False, "rollout_percent": 100},
    )
    assert resp.status_code == 403


async def test_create_feature_flag_rejects_invalid_key(client, auth_as):
    auth_as(role=UserRole.ADMIN)
    resp = await client.post(
        "/api/v1/feature-flags",
        json={
            "key": "Invalid-KEY",  # must be snake_case
            "enabled_global": False,
            "rollout_percent": 100,
        },
    )
    assert resp.status_code == 422


async def test_create_feature_flag_happy_path(client, auth_as):
    auth_as(role=UserRole.ADMIN)
    fake_flag = SimpleNamespace(
        id=uuid.uuid4(),
        key="cypress_ingest",
        description="Cypress parser flag",
        enabled_global=True,
        enabled_projects=None,
        enabled_roles=None,
        rollout_percent=100,
        created_at=None,
        updated_at=None,
        updated_by_user_id=None,
    )
    with patch(
        "app.services.feature_flags.create_flag",
        AsyncMock(return_value=fake_flag),
    ) as mock_create:
        resp = await client.post(
            "/api/v1/feature-flags",
            json={
                "key": "cypress_ingest",
                "description": "Cypress parser flag",
                "enabled_global": True,
                "rollout_percent": 100,
            },
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["key"] == "cypress_ingest"
    mock_create.assert_awaited_once()


# ── PATCH /api/v1/feature-flags/{key} ───────────────────────────────────────


async def test_update_feature_flag_passes_only_set_fields(client, auth_as):
    auth_as(role=UserRole.ADMIN)
    updated_flag = SimpleNamespace(
        id=uuid.uuid4(),
        key="cypress_ingest",
        description="Cypress parser flag",
        enabled_global=False,
        enabled_projects=None,
        enabled_roles=None,
        rollout_percent=50,
        created_at=None,
        updated_at=None,
        updated_by_user_id=None,
    )
    with patch(
        "app.services.feature_flags.update_flag",
        AsyncMock(return_value=updated_flag),
    ) as mock_update:
        resp = await client.patch(
            "/api/v1/feature-flags/cypress_ingest",
            json={"rollout_percent": 50},
        )
    assert resp.status_code == 200
    call = mock_update.await_args
    # exclude_unset means only rollout_percent propagates.
    assert call.kwargs["updates"] == {"rollout_percent": 50}


# ── DELETE /api/v1/feature-flags/{key} ──────────────────────────────────────


async def test_delete_feature_flag(client, auth_as):
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.feature_flags.delete_flag",
        AsyncMock(return_value=None),
    ) as mock_delete:
        resp = await client.delete("/api/v1/feature-flags/cypress_ingest")
    assert resp.status_code == 204
    mock_delete.assert_awaited_once()


# ── GET /api/v1/feature-flags/{key}/status ──────────────────────────────────


async def test_flag_status_any_authenticated_user(client, auth_as):
    """The status endpoint is available to non-admins — frontend uses it
    to conditionally render UI without needing ADMIN access."""
    auth_as(role=UserRole.QA_ENGINEER)
    with patch(
        "app.services.feature_flags.is_enabled",
        AsyncMock(return_value=True),
    ):
        resp = await client.get("/api/v1/feature-flags/cypress_ingest/status")
    assert resp.status_code == 200
    assert resp.json() == {"key": "cypress_ingest", "enabled": True}


async def test_flag_status_invalid_project_id_rejected(client, auth_as):
    auth_as(role=UserRole.QA_ENGINEER)
    resp = await client.get(
        "/api/v1/feature-flags/cypress_ingest/status",
        params={"project_id": "not-a-uuid"},
    )
    assert resp.status_code == 400
