"""Regression: project membership authorization must come from Postgres.

Redis membership entries used to remain authoritative for five minutes after
revocation when invalidation failed or an in-flight cache fill raced the
delete. These checks pin the shared authorization boundary to the current
``project_members`` rows and preserve the established member/admin results.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.deps import get_accessible_project_ids
from app.models.postgres import UserRole


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def _user(role: UserRole = UserRole.VIEWER):
    return SimpleNamespace(id=uuid.uuid4(), role=role)


@pytest.mark.asyncio
async def test_removed_membership_ignores_stale_redis(monkeypatch):
    """An empty authoritative result cannot be widened by a stale Redis entry."""
    stale_project = uuid.uuid4()
    redis = AsyncMock()
    redis.get.return_value = f'cached authority for {stale_project}'
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
    db = AsyncMock()
    db.execute.return_value = _Rows([])

    assert await get_accessible_project_ids(db, _user()) == set()
    redis.get.assert_not_awaited()
    redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_memberships_are_returned(monkeypatch):
    """Current Postgres memberships retain the ordinary non-admin read scope."""
    project_id = uuid.uuid4()
    redis = AsyncMock()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
    db = AsyncMock()
    db.execute.return_value = _Rows([(project_id,)])

    assert await get_accessible_project_ids(db, _user()) == {project_id}
    redis.get.assert_not_awaited()
    redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_scope_remains_unrestricted():
    """Instance administrators retain the established unrestricted sentinel."""
    db = AsyncMock()

    assert await get_accessible_project_ids(db, _user(UserRole.ADMIN)) is None
    db.execute.assert_not_awaited()
