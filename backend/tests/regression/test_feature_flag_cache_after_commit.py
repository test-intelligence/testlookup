"""Feature-flag writes invalidate caches only after their commit is visible.

Invalidating inside the service transaction opens a race: another process can
read the old committed row between invalidation and commit, then repopulate
Redis and its in-process cache with that stale value for 30 seconds.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.routers import feature_flags as router


class _Payload:
    key = "release_guard"
    description = None
    enabled_global = True
    enabled_projects = None
    enabled_roles = None
    rollout_percent = 100

    def model_dump(self, *, exclude_unset=False):
        return {"enabled_global": True}


def _db_and_invalidator():
    calls: list[str] = []

    async def commit():
        calls.append("commit")

    async def invalidate(key):
        calls.append(f"invalidate:{key}")

    db = SimpleNamespace(commit=commit)
    return calls, db, invalidate


@pytest.mark.asyncio
async def test_patch_commits_before_invalidating_shared_caches():
    calls, db, invalidate = _db_and_invalidator()

    async def update(*args, **kwargs):
        calls.append("update")
        return SimpleNamespace(key="release_guard")

    with patch.object(router.ff_service, "update_flag", new=update), patch.object(
        router.ff_service, "invalidate_flag_cache", new=invalidate,
    ):
        await router.update_feature_flag(
            "release_guard", _Payload(), db=db, current_user=SimpleNamespace(),
        )

    assert calls == ["update", "commit", "invalidate:release_guard"]


@pytest.mark.asyncio
async def test_create_commits_before_invalidating_shared_caches():
    calls, db, invalidate = _db_and_invalidator()

    async def create(*args, **kwargs):
        calls.append("create")
        return SimpleNamespace(key="release_guard")

    with patch.object(router.ff_service, "create_flag", new=create), patch.object(
        router.ff_service, "invalidate_flag_cache", new=invalidate,
    ):
        await router.create_feature_flag(
            _Payload(), db=db, current_user=SimpleNamespace(),
        )

    assert calls == ["create", "commit", "invalidate:release_guard"]


@pytest.mark.asyncio
async def test_delete_commits_before_invalidating_shared_caches():
    calls, db, invalidate = _db_and_invalidator()

    async def delete(*args, **kwargs):
        calls.append("delete")

    with patch.object(router.ff_service, "delete_flag", new=delete), patch.object(
        router.ff_service, "invalidate_flag_cache", new=invalidate,
    ):
        await router.delete_feature_flag(
            "release_guard", db=db, current_user=SimpleNamespace(),
        )

    assert calls == ["delete", "commit", "invalidate:release_guard"]


def test_mutation_services_do_not_invalidate_before_the_router_commit():
    """Guard the class: services stage writes; only routers own ordering."""
    for service in (
        router.ff_service.create_flag,
        router.ff_service.update_flag,
        router.ff_service.delete_flag,
    ):
        assert "_invalidate(" not in __import__("inspect").getsource(service)
