"""Regression: GET /api/v1/releases leaked cross-tenant via a provided project_id.

Bug pinned (review/release-service, 2026-06-02):

The list endpoint only gated the no-project_id path (returned [] for non-admins
in all-projects mode). When ``project_id`` WAS supplied it fell straight through
to ``release_service.list_releases(project_id)`` with no access check, so any
authenticated user could list another tenant's releases via
``?project_id=<foreign-uuid>``.

Fix: the router verifies the provided project_id is in the caller's accessible
set (403 otherwise, admin bypass), and ``list_releases`` now ANDs the membership
filter (defence-in-depth + all-projects fan-out).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from app.routers import releases as rel_router  # noqa: E402
from app.services import release_service as svc  # noqa: E402


# ── router: a provided project_id is access-checked ─────────────────────────


@pytest.mark.asyncio
async def test_list_releases_denies_foreign_project():
    foreign = str(uuid.uuid4())
    with patch.object(
        rel_router, "get_accessible_project_ids",
        AsyncMock(return_value={uuid.uuid4()}),  # own projects, not `foreign`
    ):
        with pytest.raises(HTTPException) as exc:
            await rel_router.list_releases(
                project_id=foreign, status=None, db=AsyncMock(),
                current_user=SimpleNamespace(id=uuid.uuid4()),
            )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_list_releases_allows_own_project_and_passes_scope():
    pid = uuid.uuid4()
    captured = {}

    async def _fake_list(db, project_id, status=None, accessible_project_ids=None):
        captured["accessible"] = accessible_project_ids
        return {"items": [], "total": 0}

    with patch.object(
        rel_router, "get_accessible_project_ids", AsyncMock(return_value={pid})
    ), patch.object(rel_router.release_service, "list_releases", _fake_list):
        await rel_router.list_releases(
            project_id=str(pid), status=None, db=AsyncMock(),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )
    # The accessible set is threaded into the service (defence-in-depth).
    assert captured["accessible"] == {pid}


@pytest.mark.asyncio
async def test_list_releases_admin_unrestricted():
    captured = {}

    async def _fake_list(db, project_id, status=None, accessible_project_ids=None):
        captured["accessible"] = accessible_project_ids
        return {"items": [], "total": 0}

    with patch.object(
        rel_router, "get_accessible_project_ids", AsyncMock(return_value=None)  # admin
    ), patch.object(rel_router.release_service, "list_releases", _fake_list):
        await rel_router.list_releases(
            project_id=str(uuid.uuid4()), status=None, db=AsyncMock(),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )
    assert captured["accessible"] is None  # no restriction for admin


# ── service: membership filter ANDed even with a project_id ──────────────────


class _Scalars:
    def all(self):
        return []


class _Res:
    def scalars(self):
        return _Scalars()

    def fetchall(self):
        return []


class _CapturingDB:
    def __init__(self):
        self.sql: list[str] = []

    async def execute(self, stmt, *a, **k):
        self.sql.append(str(stmt))
        return _Res()


@pytest.mark.asyncio
async def test_list_releases_service_ands_membership_filter():
    db = _CapturingDB()
    await svc.list_releases(
        db, str(uuid.uuid4()), None, accessible_project_ids={uuid.uuid4()},
    )
    select_sql = db.sql[0]
    # Both the equality pin and the membership IN must be present.
    assert "project_id =" in select_sql
    assert " IN (" in select_sql
