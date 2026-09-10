"""The purge signal for the pre-M15 shared AI cache can be read (code review of M15).

``legacy_unscoped_documents`` counts what is left in the similarity cache's old
collection, which every tenant shared before re-audit M15 and which may hold any
tenant's analyses. The M15 fix computed it in ``get_semantic_cache_stats``, and
its CHANGELOG entry said "the cache's health endpoint now reports" it -- but no
endpoint called that function, so nobody could see it.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jose")

from app.models.postgres import UserRole  # noqa: E402

pytestmark = pytest.mark.asyncio

URL = "/api/v1/admin/maintenance/ai-cache"
HEALTHY = {
    "status": "healthy",
    "collection": "ai_analysis_cache",
    "project_collections": 2,
    "document_count": 10,
    "legacy_unscoped_documents": 7,
}


@pytest.fixture
def stats(monkeypatch):
    def _set(value):
        async def _get():
            return value

        monkeypatch.setattr("app.services.semantic_cache.get_semantic_cache_stats", _get)

    return _set


async def test_an_instance_admin_sees_what_the_legacy_collection_holds(client, auth_as, stats):
    stats(HEALTHY)
    auth_as(role=UserRole.ADMIN)
    resp = await client.get(URL)
    assert resp.status_code == 200, resp.text
    assert resp.json()["legacy_unscoped_documents"] == 7


async def test_an_unreadable_cache_is_a_503_not_zeros(client, auth_as, stats):
    stats({
        "status": "unavailable", "error": "chroma down", "project_collections": None,
        "document_count": None, "legacy_unscoped_documents": None,
    })
    auth_as(role=UserRole.ADMIN)
    resp = await client.get(URL)
    assert resp.status_code == 503, resp.text


@pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.QA_LEAD])
async def test_only_an_instance_admin_can_read_it(client, auth_as, stats, role):
    stats(HEALTHY)
    auth_as(role=role)
    assert (await client.get(URL)).status_code == 403


async def test_an_admins_project_bound_key_cannot_read_it(client, auth_as, stats):
    """The counts span tenants; a key bound to one project is not enough."""
    stats(HEALTHY)
    auth_as(role=UserRole.ADMIN, bound_project_id=uuid.uuid4())
    assert (await client.get(URL)).status_code == 403
