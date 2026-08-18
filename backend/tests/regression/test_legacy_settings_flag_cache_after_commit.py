"""Legacy settings flag writes must invalidate the canonical shared cache."""
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# The route under test does not use SMTP; keep this focused guard runnable in
# minimal local environments where that optional transport is not installed.
sys.modules.setdefault("aiosmtplib", MagicMock())

from app.routers import app_settings as router  # noqa: E402


def _db_and_invalidator():
    calls: list[str] = []

    async def commit():
        calls.append("commit")

    async def invalidate(key):
        calls.append(f"invalidate:{key}")

    return calls, SimpleNamespace(commit=commit), invalidate


def _audit_recorder(calls):
    async def audit(db, key, action, actor, changed_fields):
        calls.append(f"audit:{key}:{action}:{','.join(changed_fields)}")

    return audit


@pytest.mark.asyncio
async def test_legacy_put_commits_before_invalidating_shared_cache():
    calls, db, invalidate = _db_and_invalidator()

    async def set_flag(*args, **kwargs):
        calls.append("set")
        return {"flag_key": "release_guard", "enabled": True}

    body = router.FeatureFlagUpdate(enabled=True)
    with patch("app.services.feature_flag_service.set_flag", new=set_flag), patch.object(
        router, "log_settings_change", new=_audit_recorder(calls),
    ), patch(
        "app.services.feature_flags.invalidate_flag_cache", new=invalidate,
    ):
        await router.update_feature_flag(
            "release_guard", body, db=db, current_user=SimpleNamespace(),
        )

    assert calls == [
        "set",
        "audit:feature_flag:release_guard:updated:enabled_global",
        "commit",
        "invalidate:release_guard",
    ]


@pytest.mark.asyncio
async def test_legacy_delete_commits_before_invalidating_shared_cache():
    calls, db, invalidate = _db_and_invalidator()

    async def delete_flag(*args, **kwargs):
        calls.append("delete")
        return True

    with patch("app.services.feature_flag_service.delete_flag", new=delete_flag), patch.object(
        router, "log_settings_change", new=_audit_recorder(calls),
    ), patch(
        "app.services.feature_flags.invalidate_flag_cache", new=invalidate,
    ):
        await router.remove_feature_flag(
            "release_guard", db=db, current_user=SimpleNamespace(),
        )

    assert calls == [
        "delete",
        "audit:feature_flag:release_guard:deleted:enabled_global",
        "commit",
        "invalidate:release_guard",
    ]


@pytest.mark.asyncio
async def test_legacy_delete_does_not_audit_a_missing_flag():
    calls, db, invalidate = _db_and_invalidator()

    async def delete_flag(*args, **kwargs):
        calls.append("delete-miss")
        return False

    with patch("app.services.feature_flag_service.delete_flag", new=delete_flag), patch.object(
        router, "log_settings_change", new=_audit_recorder(calls),
    ), patch(
        "app.services.feature_flags.invalidate_flag_cache", new=invalidate,
    ):
        await router.remove_feature_flag(
            "missing", db=db, current_user=SimpleNamespace(),
        )

    assert calls == ["delete-miss", "commit", "invalidate:missing"]
