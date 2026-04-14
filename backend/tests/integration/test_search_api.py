"""
API integration tests for /api/v1/search and /api/v1/search/global.

Verifies the tenant-isolation fix (resolve_project_scope wired into both
endpoints) and the keyword search path. Search service results are
mocked so the tests don't need real test data.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jose")
pytest.importorskip("asyncpg")

from app.models.postgres import UserRole  # noqa: E402

pytestmark = pytest.mark.asyncio


# ── /api/v1/search (test case keyword search) ───────────────────────────────


async def test_search_keyword_happy_path(client, auth_as):
    project_id = uuid.uuid4()
    auth_as(accessible_projects={project_id})

    fake_results = (
        [{"test_case_id": str(uuid.uuid4()), "test_name": "login_test", "status": "FAILED"}],
        1,
        1,
    )
    with patch(
        "app.routers.search.search_test_cases_query",
        AsyncMock(return_value=fake_results),
    ):
        resp = await client.get(
            "/api/v1/search",
            params={"q": "login", "project_id": str(project_id)},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["search_type"] == "keyword"
    assert len(body["items"]) == 1


async def test_search_rejects_non_member_project(client, auth_as):
    """
    Tenant isolation regression: a non-admin user requesting a project
    they don't belong to must get 403 from resolve_project_scope.
    """
    auth_as(accessible_projects={uuid.uuid4()})
    forbidden = uuid.uuid4()

    resp = await client.get(
        "/api/v1/search",
        params={"q": "anything", "project_id": str(forbidden)},
    )
    assert resp.status_code == 403


async def test_search_non_admin_no_project_scopes_to_memberships(client, auth_as):
    """
    Non-admin without project_id should fan out to their accessible set
    instead of getting an empty response (the previous broken behavior).
    The service must be called with allowed_project_ids set.
    """
    member_set = {uuid.uuid4(), uuid.uuid4()}
    auth_as(accessible_projects=member_set)

    captured = {}

    async def _fake_query(*args, **kwargs):
        captured.update(kwargs)
        return ([], 0, 0)

    with patch("app.routers.search.search_test_cases_query", _fake_query):
        resp = await client.get("/api/v1/search", params={"q": "x"})

    assert resp.status_code == 200
    # The router must pass the accessible set through to the service.
    assert captured.get("allowed_project_ids") == member_set
    # And it must NOT pin to a single project_id.
    assert captured.get("project_id") in (None, "None")


async def test_search_admin_unrestricted(client, auth_as):
    auth_as(role=UserRole.ADMIN)
    captured = {}

    async def _fake_query(*args, **kwargs):
        captured.update(kwargs)
        return ([], 0, 0)

    with patch("app.routers.search.search_test_cases_query", _fake_query):
        resp = await client.get("/api/v1/search", params={"q": "x"})

    assert resp.status_code == 200
    # Admin: no restrictions — neither pin nor allowed-set.
    assert captured.get("project_id") is None
    assert captured.get("allowed_project_ids") is None


async def test_search_invalid_uuid_rejected(client, auth_as):
    auth_as()
    resp = await client.get(
        "/api/v1/search",
        params={"q": "x", "project_id": "not-a-uuid"},
    )
    assert resp.status_code == 400


# ── /api/v1/search/global ───────────────────────────────────────────────────


async def test_global_search_happy_path(client, auth_as):
    project_id = uuid.uuid4()
    auth_as(accessible_projects={project_id})

    fake_response = {
        "items": [{"entity_type": "test_case", "entity_id": str(uuid.uuid4()), "title": "x"}],
        "total": 1,
        "query": "x",
        "search_type": "keyword",
        "entity_counts": {"test_case": 1},
        "page": 1,
        "size": 20,
        "pages": 1,
    }
    with patch(
        "app.services.global_search_service.global_search",
        AsyncMock(return_value=fake_response),
    ):
        resp = await client.get(
            "/api/v1/search/global",
            params={"q": "x", "project_id": str(project_id)},
        )

    assert resp.status_code == 200
    assert resp.json()["total"] == 1


async def test_global_search_non_member_rejected(client, auth_as):
    auth_as(accessible_projects={uuid.uuid4()})
    resp = await client.get(
        "/api/v1/search/global",
        params={"q": "x", "project_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 403


async def test_global_search_q_required(client, auth_as):
    auth_as(role=UserRole.ADMIN)
    resp = await client.get("/api/v1/search/global", params={})
    # Pydantic Query(..., min_length=1)
    assert resp.status_code == 422
