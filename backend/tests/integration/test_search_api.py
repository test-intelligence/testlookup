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


@pytest.mark.parametrize(
    ("search_type", "primary_patch"),
    [
        ("semantic", "app.services.semantic_search.semantic_search"),
        ("hybrid", "app.services.semantic_search.hybrid_search"),
    ],
)
async def test_search_reports_keyword_when_semantic_mode_falls_back(
    client, auth_as, search_type, primary_patch,
):
    """A non-empty fallback result must not be mislabeled as semantic."""
    auth_as(role=UserRole.ADMIN)

    primary = AsyncMock(return_value=([], 0, 0))
    keyword = AsyncMock(
        return_value=([{"test_case_id": str(uuid.uuid4())}], 1, 1),
    )
    with (
        patch(primary_patch, primary),
        patch("app.routers.search.search_test_cases_query", keyword),
    ):
        resp = await client.get(
            "/api/v1/search",
            params={"q": "login", "search_type": search_type},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1
    assert resp.json()["search_type"] == "keyword"
    primary.assert_awaited_once()
    keyword.assert_awaited_once()


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


@pytest.mark.parametrize("entity_types", ["bogus", "test_case,bogus"])
async def test_global_search_rejects_unknown_entity_types(
    client, auth_as, entity_types,
):
    """An invalid explicit filter must not broaden into all adapters."""
    auth_as(role=UserRole.ADMIN)
    response = {
        "items": [], "total": 0, "query": "x", "search_type": "keyword",
        "entity_counts": {}, "page": 1, "size": 20, "pages": 0,
    }
    with patch(
        "app.services.global_search_service.global_search",
        AsyncMock(return_value=response),
    ) as global_search:
        resp = await client.get(
            "/api/v1/search/global",
            params={"q": "x", "entity_types": entity_types},
        )

    assert resp.status_code == 400
    assert "bogus" in resp.json()["detail"]
    global_search.assert_not_awaited()


async def test_global_search_forwards_valid_entity_filter(client, auth_as):
    auth_as(role=UserRole.ADMIN)
    response = {
        "items": [], "total": 0, "query": "x", "search_type": "keyword",
        "entity_counts": {}, "page": 1, "size": 20, "pages": 0,
    }
    with patch(
        "app.services.global_search_service.global_search",
        AsyncMock(return_value=response),
    ) as global_search:
        resp = await client.get(
            "/api/v1/search/global",
            params={"q": "x", "entity_types": "test_case,release"},
        )

    assert resp.status_code == 200
    assert global_search.await_args.kwargs["entity_types"] == {
        "test_case", "release",
    }


async def test_global_search_empty_q_browses_not_422(client, auth_as):
    """``q`` is optional now — an empty query BROWSES the most-recent
    items in scope (the /search page chips with no query typed) rather
    than 422'ing. The endpoint signature changed from
    ``Query(..., min_length=1)`` to ``Query("")`` with browse semantics,
    so an empty query must return 200, not 422. Dedicated browse
    behaviour is covered in ``test_global_search_browse.py``."""
    auth_as(role=UserRole.ADMIN)
    fake_response = {
        "items": [],
        "total": 0,
        "query": "",
        "search_type": "keyword",
        "entity_counts": {},
        "page": 1,
        "size": 20,
        "pages": 0,
    }
    with patch(
        "app.services.global_search_service.global_search",
        AsyncMock(return_value=fake_response),
    ):
        resp = await client.get("/api/v1/search/global", params={})
    assert resp.status_code == 200
