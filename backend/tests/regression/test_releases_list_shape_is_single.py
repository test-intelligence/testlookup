"""HYG-10: ``GET /api/v1/releases`` answers one shape, ``{items, total}``.

``release_service.list_releases`` returns ``{"items": [...], "total": n}``,
but the router's no-membership branch (a non-admin with no projects, in the
all-projects view) returned a bare ``[]``. The client types and reads the list
as an object (``data.items``, ``data.total``), which read ``undefined`` off the
array, and the in-app guide promised a paged ``{items, total, page, size,
pages}`` that this endpoint never sends.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.routers import releases as rel_router  # noqa: E402

pytestmark = pytest.mark.regression


def _member():
    return SimpleNamespace(id=uuid.uuid4(), role="VIEWER", is_active=True, api_key_project_id=None)


@pytest.mark.asyncio
async def test_no_memberships_answers_the_object_shape_without_querying():
    service = AsyncMock()
    with patch.object(rel_router, "get_accessible_project_ids", AsyncMock(return_value=set())), \
            patch.object(rel_router.release_service, "list_releases", service):
        body = await rel_router.list_releases(project_id=None, status=None, db=AsyncMock(), current_user=_member())

    assert body == {"items": [], "total": 0}
    service.assert_not_awaited()


@pytest.mark.asyncio
async def test_every_branch_answers_the_same_keys():
    """The member branch passes the service's answer through; both branches
    must carry the same keys, so the client has one shape to read."""
    pid = uuid.uuid4()
    served = {"items": [{"id": str(uuid.uuid4()), "name": "2026.11"}], "total": 1}
    with patch.object(rel_router, "get_accessible_project_ids", AsyncMock(return_value={pid})), \
            patch.object(rel_router.release_service, "list_releases", AsyncMock(return_value=served)):
        member = await rel_router.list_releases(project_id=None, status=None, db=AsyncMock(), current_user=_member())
    with patch.object(rel_router, "get_accessible_project_ids", AsyncMock(return_value=set())):
        empty = await rel_router.list_releases(project_id=None, status=None, db=AsyncMock(), current_user=_member())

    assert set(member) == set(empty) == {"items", "total"}


def test_the_service_returns_the_same_shape():
    import inspect

    from app.services import release_service

    src = inspect.getsource(release_service.list_releases)
    assert 'return {"items": items, "total": len(items)}' in src
