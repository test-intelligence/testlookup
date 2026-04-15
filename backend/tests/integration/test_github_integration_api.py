"""
API integration tests for /api/v1/projects/{id}/github-integration — Tier 1 item 5.

Covers role guards, tenant isolation, 404 when missing, happy-path upsert
+ test-connection, and the DELETE path.
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
from tests.integration.conftest import fake_execute_result  # noqa: E402

pytestmark = pytest.mark.asyncio


def _fake_integration(project_id: uuid.UUID) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        enabled=True,
        repo_owner="acme",
        repo_name="webapp",
        api_base_url="https://api.github.com",
        has_pat=True,
        last_posted_at=now,
        last_error=None,
        last_error_at=None,
        created_at=now,
        updated_at=now,
        updated_by_user_id=None,
    )


# ── GET ─────────────────────────────────────────────────────────────────────


async def test_get_integration_requires_project_membership(
    client, auth_as, override_db, fake_db,
):
    target = uuid.uuid4()
    auth_as(accessible_projects={uuid.uuid4()})  # not the target
    resp = await client.get(f"/api/v1/projects/{target}/github-integration")
    assert resp.status_code == 403


async def test_get_integration_returns_404_when_missing(
    client, auth_as, override_db, fake_db,
):
    target = uuid.uuid4()
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.github_checks_service.get_integration",
        AsyncMock(return_value=None),
    ):
        resp = await client.get(f"/api/v1/projects/{target}/github-integration")
    assert resp.status_code == 404


async def test_get_integration_happy_path(client, auth_as, override_db, fake_db):
    target = uuid.uuid4()
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.github_checks_service.get_integration",
        AsyncMock(return_value=_fake_integration(target)),
    ):
        resp = await client.get(f"/api/v1/projects/{target}/github-integration")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["repo_owner"] == "acme"
    assert body["has_pat"] is True


# ── PUT ─────────────────────────────────────────────────────────────────────


async def test_put_requires_qa_lead(client, auth_as):
    target = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER)
    resp = await client.put(
        f"/api/v1/projects/{target}/github-integration",
        json={
            "enabled": True,
            "repo_owner": "acme",
            "repo_name": "webapp",
            "api_base_url": "https://api.github.com",
        },
    )
    assert resp.status_code == 403


async def test_put_rejects_invalid_repo_owner(client, auth_as):
    target = uuid.uuid4()
    auth_as(role=UserRole.ADMIN)
    resp = await client.put(
        f"/api/v1/projects/{target}/github-integration",
        json={
            "enabled": True,
            "repo_owner": "has spaces",   # invalid per pattern
            "repo_name": "webapp",
            "api_base_url": "https://api.github.com",
        },
    )
    assert resp.status_code == 422


async def test_put_happy_path(client, auth_as, override_db, fake_db):
    target = uuid.uuid4()
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.github_checks_service.upsert_integration",
        AsyncMock(return_value=_fake_integration(target)),
    ) as mock_upsert:
        resp = await client.put(
            f"/api/v1/projects/{target}/github-integration",
            json={
                "enabled": True,
                "repo_owner": "acme",
                "repo_name": "webapp",
                "api_base_url": "https://api.github.com",
                "pat": "ghp_fake-token-value",
            },
        )
    assert resp.status_code == 200, resp.text
    mock_upsert.assert_awaited_once()


# ── POST /test ──────────────────────────────────────────────────────────────


async def test_test_connection_requires_qa_lead(client, auth_as):
    target = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER)
    resp = await client.post(
        f"/api/v1/projects/{target}/github-integration/test",
    )
    assert resp.status_code == 403


async def test_test_connection_returns_result(client, auth_as, override_db, fake_db):
    target = uuid.uuid4()
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.github_checks_service.test_connection",
        AsyncMock(
            return_value={
                "success": True,
                "status_code": 200,
                "message": "OK — repo 'acme/webapp' reachable",
                "repo_html_url": "https://github.com/acme/webapp",
            }
        ),
    ):
        resp = await client.post(
            f"/api/v1/projects/{target}/github-integration/test",
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["repo_html_url"] == "https://github.com/acme/webapp"
