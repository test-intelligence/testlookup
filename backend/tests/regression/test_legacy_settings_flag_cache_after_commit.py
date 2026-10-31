"""Legacy settings flag writes: authority lock first, cache invalidation after commit.

Since W25b the legacy ``PUT`` / ``DELETE /api/v1/settings/flags/{key}`` are
aliases over the canonical ``services/feature_flags``. The order is the
contract (§2.1 A-2 / A-3): take the global authority lock, probe the key, then
create / update / delete, write the route's settings audit row, commit, and
only then drop the shared cache, so every replica observes the new row and no
concurrent reader repopulates Redis from old data.
"""
import sys
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# The route under test does not use SMTP; keep this focused guard runnable in
# minimal local environments where that optional transport is not installed.
sys.modules.setdefault("aiosmtplib", MagicMock())

from app.routers import app_settings as router  # noqa: E402


def _recorders(calls: list[str], *, existing: bool):
    """Async recorders for the lock, the canonical service and the route's audit."""

    async def lock(db):
        calls.append("lock")

    async def get_flag(db, key):
        calls.append("get")
        return SimpleNamespace(key=key) if existing else None

    async def create_flag(db, **kwargs):
        calls.append("create")

    async def update_flag(db, **kwargs):
        calls.append("update")

    async def delete_flag(db, **kwargs):
        calls.append("delete")

    async def invalidate(key):
        calls.append(f"invalidate:{key}")

    async def audit(db, key, action, actor, changed_fields):
        calls.append(f"audit:{key}:{action}:{','.join(changed_fields)}")

    return {
        "app.services.agent_authority_lock.lock_global_agent_authority": lock,
        "app.services.feature_flags.get_flag": get_flag,
        "app.services.feature_flags.create_flag": create_flag,
        "app.services.feature_flags.update_flag": update_flag,
        "app.services.feature_flags.delete_flag": delete_flag,
        "app.services.feature_flags.invalidate_flag_cache": invalidate,
    }, audit


def _db(calls: list[str]):
    async def commit():
        calls.append("commit")

    return SimpleNamespace(commit=commit)


async def _run(handler, *args, existing: bool) -> list[str]:
    calls: list[str] = []
    targets, audit = _recorders(calls, existing=existing)
    with ExitStack() as stack:
        for target, recorder in targets.items():
            stack.enter_context(patch(target, new=recorder))
        stack.enter_context(patch.object(router, "log_settings_change", new=audit))
        await handler(*args, db=_db(calls), current_user=SimpleNamespace(id=None))
    return calls


@pytest.mark.asyncio
async def test_legacy_put_of_an_existing_key_locks_updates_then_commits_before_invalidating():
    body = router.FeatureFlagUpdate(enabled=True)
    calls = await _run(router.update_feature_flag, "release_guard", body, existing=True)

    assert calls == [
        "lock",
        "get",
        "update",
        "audit:feature_flag:release_guard:updated:enabled_global",
        "commit",
        "invalidate:release_guard",
    ]


@pytest.mark.asyncio
async def test_legacy_put_of_an_unknown_key_locks_creates_then_commits_before_invalidating():
    body = router.FeatureFlagUpdate(enabled=True)
    calls = await _run(router.update_feature_flag, "release_guard", body, existing=False)

    assert calls == [
        "lock",
        "get",
        "create",
        "audit:feature_flag:release_guard:updated:enabled_global",
        "commit",
        "invalidate:release_guard",
    ]


@pytest.mark.asyncio
async def test_legacy_delete_of_an_existing_key_locks_deletes_then_commits_before_invalidating():
    calls = await _run(router.remove_feature_flag, "release_guard", existing=True)

    assert calls == [
        "lock",
        "get",
        "delete",
        "audit:feature_flag:release_guard:deleted:enabled_global",
        "commit",
        "invalidate:release_guard",
    ]


@pytest.mark.asyncio
async def test_legacy_delete_of_a_missing_key_calls_no_service_and_writes_no_audit():
    calls = await _run(router.remove_feature_flag, "missing", existing=False)

    assert calls == ["lock", "get", "commit", "invalidate:missing"]
