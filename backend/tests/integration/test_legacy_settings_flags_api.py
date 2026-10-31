"""W25b HYG-03 — the legacy ``/api/v1/settings/flags`` routes are DEPRECATED aliases.

Since W25b the three routes run over the canonical ``services/feature_flags``
(the service behind ``/api/v1/feature-flags``); the facade module they used to
call is deleted. Paths, roles, request bodies and response shapes are
unchanged; every 2xx answer carries ``Deprecation`` (RFC 9745 date) and a
``Link`` to the successor, and OpenAPI marks the operations deprecated.

HTTP through the in-process app with the integration fixtures (fake session,
scripted auth); the canonical functions and the authority lock are patched, so
no PostgreSQL is needed. The real-session proof is
``test_legacy_settings_flags_alias_postgres.py``.
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jwt")
pytest.importorskip("asyncpg")

from app.models.postgres import UserRole  # noqa: E402

# asyncio_mode = auto (pytest.ini): the async tests need no mark, the two sync ones must not carry one.
DEPRECATION = "@1793491200"
LINK = '</api/v1/feature-flags>; rel="successor-version"'
_SVC = "app.services.feature_flags"
_LOCK = "app.services.agent_authority_lock.lock_global_agent_authority"


def _flag(key: str, *, enabled: bool = True, description: str | None = "probe", updated_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(), key=key, description=description, enabled_global=enabled,
        enabled_projects=None, enabled_roles=None, rollout_percent=100, updated_at=updated_at,
    )


def _assert_deprecation_headers(resp) -> None:
    assert resp.headers.get("deprecation") == DEPRECATION
    assert resp.headers.get("link") == LINK


class _Canonical:
    """The canonical service + lock patched with AsyncMocks for one request."""

    def __init__(self, *, existing=None, listed=None):
        self.lock = AsyncMock()
        self.list_flags = AsyncMock(return_value=listed or [])
        self.get_flag = AsyncMock(return_value=existing)
        self.create_flag = AsyncMock()
        self.update_flag = AsyncMock()
        self.delete_flag = AsyncMock()
        self.invalidate = AsyncMock()
        self.audit = AsyncMock()
        self._patches = [
            patch(_LOCK, new=self.lock),
            patch(f"{_SVC}.list_flags", new=self.list_flags),
            patch(f"{_SVC}.get_flag", new=self.get_flag),
            patch(f"{_SVC}.create_flag", new=self.create_flag),
            patch(f"{_SVC}.update_flag", new=self.update_flag),
            patch(f"{_SVC}.delete_flag", new=self.delete_flag),
            patch(f"{_SVC}.invalidate_flag_cache", new=self.invalidate),
            patch("app.routers.app_settings.log_settings_change", new=self.audit),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False

    def no_write(self) -> None:
        for mock in (self.create_flag, self.update_flag, self.delete_flag, self.audit):
            mock.assert_not_awaited()


# ── GET /api/v1/settings/flags ──────────────────────────────────────────────


async def test_get_as_qa_lead_answers_the_legacy_shape_with_both_headers(client, auth_as, fake_db):
    auth_as(role=UserRole.QA_LEAD)
    stamp = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    rows = [_flag("alpha_gate", enabled=True, updated_at=stamp),
            _flag("beta_gate", enabled=False, description=None, updated_at=None)]
    with _Canonical(listed=rows) as svc:
        resp = await client.get("/api/v1/settings/flags")

    assert resp.status_code == 200, resp.text
    _assert_deprecation_headers(resp)
    assert resp.json() == [
        {"flag_key": "alpha_gate", "scope": "global", "enabled": True, "config": None,
         "description": "probe", "updated_at": stamp.isoformat()},
        {"flag_key": "beta_gate", "scope": "global", "enabled": False, "config": None,
         "description": None, "updated_at": None},
    ]
    svc.list_flags.assert_awaited_once()
    svc.no_write()
    svc.lock.assert_not_awaited()
    assert fake_db.committed == 0  # a GET never writes


async def test_get_as_tester_is_refused(client, auth_as):
    auth_as(role=UserRole.TESTER)
    with _Canonical() as svc:
        resp = await client.get("/api/v1/settings/flags")
    assert resp.status_code == 403
    svc.list_flags.assert_not_awaited()


# ── PUT /api/v1/settings/flags/{flag_key} ───────────────────────────────────


async def test_put_as_qa_lead_is_refused(client, auth_as):
    auth_as(role=UserRole.QA_LEAD)
    with _Canonical() as svc:
        resp = await client.put("/api/v1/settings/flags/release_guard", json={"enabled": True})
    assert resp.status_code == 403
    svc.lock.assert_not_awaited()
    svc.no_write()


async def test_put_with_a_key_over_80_characters_is_422_before_any_service_call(client, auth_as, fake_db):
    auth_as(role=UserRole.ADMIN)
    with _Canonical() as svc:
        ok_len = await client.put(f"/api/v1/settings/flags/{'k' * 80}", json={"enabled": True})
        resp = await client.put(f"/api/v1/settings/flags/{'k' * 81}", json={"enabled": True})
    assert ok_len.status_code == 200, ok_len.text  # 80 is the column's length, still accepted
    assert resp.status_code == 422, resp.text
    # Only the 80-character request reached the service.
    assert svc.lock.await_count == 1
    assert svc.get_flag.await_count == 1
    assert fake_db.committed == 1


async def test_put_existing_key_updates_enabled_only(client, auth_as, fake_db):
    admin = auth_as(role=UserRole.ADMIN)
    with _Canonical(existing=_flag("release_guard", enabled=False)) as svc:
        resp = await client.put(
            "/api/v1/settings/flags/release_guard",
            json={"enabled": True, "scope": "project", "config": {"ignored": 1}},
        )

    assert resp.status_code == 200, resp.text
    _assert_deprecation_headers(resp)
    assert resp.json() == {"flag_key": "release_guard", "enabled": True, "scope": "global"}
    svc.lock.assert_awaited_once()
    svc.update_flag.assert_awaited_once()
    assert svc.update_flag.await_args.kwargs == {
        "key": "release_guard", "updates": {"enabled_global": True}, "actor": admin,
    }
    svc.create_flag.assert_not_awaited()
    svc.audit.assert_awaited_once()
    assert svc.audit.await_args.args[1:] == ("feature_flag:release_guard", "updated", admin, ["enabled_global"])
    svc.invalidate.assert_awaited_once_with("release_guard")
    assert fake_db.committed == 1


async def test_put_existing_key_with_a_description_updates_both(client, auth_as):
    admin = auth_as(role=UserRole.ADMIN)
    with _Canonical(existing=_flag("release_guard")) as svc:
        resp = await client.put(
            "/api/v1/settings/flags/release_guard", json={"enabled": False, "description": "new words"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"flag_key": "release_guard", "enabled": False, "scope": "global"}
    assert svc.update_flag.await_args.kwargs["updates"] == {"enabled_global": False, "description": "new words"}
    assert svc.audit.await_args.args[1:] == (
        "feature_flag:release_guard", "updated", admin, ["enabled_global", "description"],
    )


async def test_put_unknown_key_creates_it_with_the_facades_defaults(client, auth_as, fake_db):
    admin = auth_as(role=UserRole.ADMIN)
    with _Canonical(existing=None) as svc:
        resp = await client.put(
            "/api/v1/settings/flags/brand_new_gate", json={"enabled": True, "description": "from a script"},
        )

    assert resp.status_code == 200, resp.text
    _assert_deprecation_headers(resp)
    assert resp.json() == {"flag_key": "brand_new_gate", "enabled": True, "scope": "global"}
    svc.create_flag.assert_awaited_once()
    assert svc.create_flag.await_args.kwargs == {
        "key": "brand_new_gate",
        "description": "from a script",
        "enabled_global": True,
        "actor": admin,
        # THE pinned defaults: a global toggle, no allow-list, everyone.
        "enabled_projects": None,
        "enabled_roles": None,
        "rollout_percent": 100,
    }
    svc.update_flag.assert_not_awaited()
    svc.invalidate.assert_awaited_once_with("brand_new_gate")
    assert fake_db.committed == 1


# ── DELETE /api/v1/settings/flags/{flag_key} ────────────────────────────────


async def test_delete_existing_key_is_204_with_both_headers(client, auth_as, fake_db):
    admin = auth_as(role=UserRole.ADMIN)
    with _Canonical(existing=_flag("release_guard")) as svc:
        resp = await client.delete("/api/v1/settings/flags/release_guard")

    assert resp.status_code == 204, resp.text
    assert resp.content == b""
    _assert_deprecation_headers(resp)
    svc.lock.assert_awaited_once()
    assert svc.delete_flag.await_args.kwargs == {"key": "release_guard", "actor": admin}
    assert svc.audit.await_args.args[1:] == ("feature_flag:release_guard", "deleted", admin, ["enabled_global"])
    svc.invalidate.assert_awaited_once_with("release_guard")
    assert fake_db.committed == 1


async def test_delete_missing_key_is_still_204_with_no_service_call_and_no_audit(client, auth_as):
    auth_as(role=UserRole.ADMIN)
    with _Canonical(existing=None) as svc:
        resp = await client.delete("/api/v1/settings/flags/missing_gate")

    assert resp.status_code == 204, resp.text
    _assert_deprecation_headers(resp)
    svc.delete_flag.assert_not_awaited()
    svc.audit.assert_not_awaited()
    svc.invalidate.assert_awaited_once_with("missing_gate")


async def test_delete_as_qa_lead_is_refused_without_headers(client, auth_as):
    auth_as(role=UserRole.QA_LEAD)
    with _Canonical(existing=_flag("release_guard")) as svc:
        resp = await client.delete("/api/v1/settings/flags/release_guard")
    assert resp.status_code == 403
    assert "deprecation" not in resp.headers  # error answers carry none
    svc.no_write()


# ── OpenAPI + the facade's absence ──────────────────────────────────────────


def test_openapi_marks_the_three_operations_deprecated():
    from app.main import app

    paths = app.openapi()["paths"]
    assert paths["/api/v1/settings/flags"]["get"].get("deprecated") is True
    item = paths["/api/v1/settings/flags/{flag_key}"]
    assert item["put"].get("deprecated") is True
    assert item["delete"].get("deprecated") is True
    # the successor stays current
    assert paths["/api/v1/feature-flags"]["get"].get("deprecated") is not True


def test_the_legacy_facade_module_is_gone():
    assert importlib.util.find_spec("app.services.feature_flag_service") is None
