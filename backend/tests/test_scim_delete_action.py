"""W38 (W31 F-2): what a SCIM ``DELETE`` does — ``services/scim_delete_setting.py`` and ``GET`` / ``PUT
/api/v1/settings/scim-delete-action`` (``routers/app_settings.py``; contract D-8, §3).

The real router on a bare FastAPI app over the SQLite world of ``test_subject_erasure_service.py`` (``get_db`` /
``get_current_active_user`` overridden; ``require_role`` real). No row = deactivate; an unreadable stored value reads
as deactivate (+ a warning); ADMIN only; the PUT needs an interactive login (an API key → 403
``instance_interactive_only``); anything but deactivate / erase → the coded 422; a change = ONE commit holding the
row, ONE ``settings_audit_log`` row and ONE identity event ``{before, after}``; the same value again writes nothing;
the GET writes nothing.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.deps import CREDENTIAL_KIND_JWT, _bind_credential_kind, get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import AppSetting, IdentityEvent, IdentityEventType, SettingsAuditLog, User, UserRole
from app.routers import app_settings as routes
from app.services import scim_delete_setting as setting
from app.services.retention_vocab import POLICY_ERROR_SENTENCES
from tests.test_subject_erasure_service import _api_key, _user
from tests.test_subject_erasure_service import maker as _sqlite_maker
from tests.test_subject_erasure_service import world as _sqlite_world

maker = pytest.fixture(name="maker")(_sqlite_maker.__wrapped__)  # type: ignore[attr-defined]
world = pytest.fixture(name="world")(_sqlite_world.__wrapped__)  # type: ignore[attr-defined]

PATH = "/api/v1/settings/scim-delete-action"
SERVICE_PY = Path(__file__).resolve().parents[1] / "app" / "services" / "scim_delete_setting.py"
INVALID = {"code": "scim_delete_action_invalid", "message": "Choose deactivate or erase."}


def _jwt(user: User) -> User:
    return _bind_credential_kind(user, CREDENTIAL_KIND_JWT)


def _client(maker: Any, user: Any, commits: list[str]) -> AsyncClient:
    app = FastAPI()
    app.include_router(routes.router)

    async def _db() -> Any:
        async with maker.session() as session:
            original = session.commit

            async def _counted() -> None:
                commits.append("commit")
                await original()

            session.commit = _counted  # type: ignore[method-assign]
            try:
                yield session
                await original()   # get_db's own end-of-request commit (not counted)
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _count(maker: Any, model: Any) -> int:
    async with maker.session() as db:
        return int((await db.execute(select(func.count()).select_from(model))).scalar() or 0)


async def _nothing_written(maker: Any) -> None:
    for model in (AppSetting, SettingsAuditLog, IdentityEvent):
        assert await _count(maker, model) == 0, model.__name__


async def _store(maker: Any, value: Any, by: Any = None) -> None:
    async with maker.session() as db:
        db.add(AppSetting(key=setting.APP_SETTINGS_KEY, value=value, updated_by=by))
        await db.commit()


def test_the_sqlite_world_holds_every_table_this_needs(maker: Any) -> None:
    assert {"app_settings", "settings_audit_log", "identity_events", "users"} <= maker.created


# ── reads ────────────────────────────────────────────────────────────────────

async def test_no_row_reads_deactivate_and_the_get_writes_nothing(maker: Any, world: SimpleNamespace) -> None:
    commits: list[str] = []
    for user in (_jwt(world.admin), _api_key(_user(UserRole.ADMIN, "zqxwkeyadmin"))):   # an admin key may READ
        async with _client(maker, user, commits) as client:
            response = await client.get(PATH)
        assert response.status_code == 200, response.text
        assert response.json() == {"action": "deactivate", "updated_at": None, "updated_by_display": None}
    assert commits == []
    await _nothing_written(maker)
    async with maker.session() as db:
        assert await setting.get_action(db) == "deactivate"


@pytest.mark.parametrize("value", [{"action": "obliterate"}, {"action": None}, {}, ["erase"], "erase", None])
async def test_an_unreadable_stored_value_reads_as_deactivate(
    maker: Any, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, value: Any,
) -> None:
    warned: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(setting, "_slog", SimpleNamespace(
        warning=lambda event, **kw: warned.append((event, kw)), info=lambda *a, **k: None))
    await _store(maker, value, by=world.admin.id)
    async with maker.session() as db:
        assert await setting.get_action(db) == "deactivate"
    async with _client(maker, _jwt(world.admin), []) as client:
        response = await client.get(PATH)
    assert response.json()["action"] == "deactivate" and response.json()["updated_by_display"] == "Zqxwadmin"
    assert warned and all(event == "scim_delete_action_unreadable" for event, _ in warned)
    assert "obliterate" not in repr(warned)   # never the stored value itself


async def test_an_erased_writer_reads_as_erased_user(maker: Any, world: SimpleNamespace) -> None:
    async with maker.session() as db:
        (await db.get(User, world.admin.id)).erased_at = datetime.now(timezone.utc)
        await db.commit()
    await _store(maker, {"action": "erase"}, by=world.admin.id)
    async with maker.session() as db:
        read = await setting.read_setting(db)
    assert (read.action, read.updated_by_display) == ("erase", "Erased user") and read.updated_at is not None


# ── who may change it ────────────────────────────────────────────────────────

@pytest.mark.parametrize("method", ["GET", "PUT"])
@pytest.mark.parametrize("role", [UserRole.QA_LEAD, UserRole.TESTER, UserRole.VIEWER])
async def test_both_routes_are_admin_only(maker: Any, world: SimpleNamespace, role: UserRole, method: str) -> None:
    commits: list[str] = []
    async with _client(maker, _jwt(_user(role, "zqxwnonadmin")), commits) as client:
        response = await (client.get(PATH) if method == "GET" else client.put(PATH, json={"action": "erase"}))
    assert response.status_code == 403, response.text
    assert commits == []
    await _nothing_written(maker)


async def test_an_api_key_cannot_change_it(maker: Any, world: SimpleNamespace) -> None:
    commits: list[str] = []
    async with _client(maker, _api_key(_user(UserRole.ADMIN, "zqxwkeyadmin")), commits) as client:
        refused = await client.put(PATH, json={"action": "erase"})
        bad_and_keyed = await client.put(PATH, json={"action": "nope"})   # the session check comes first
    for response in (refused, bad_and_keyed):
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == {
            "code": "instance_interactive_only", "message": POLICY_ERROR_SENTENCES["instance_interactive_only"],
        }
    assert commits == []
    await _nothing_written(maker)


@pytest.mark.parametrize("payload", [
    {"action": "delete"}, {"action": ""}, {"action": "Erase"}, {"action": None}, {"action": 1}, {},
    {"action": ["erase"]}, None,
])
async def test_a_bad_value_is_the_coded_422_and_writes_nothing(maker: Any, world: SimpleNamespace,
                                                               payload: Any) -> None:
    commits: list[str] = []
    async with _client(maker, _jwt(world.admin), commits) as client:
        response = await (client.put(PATH) if payload is None else client.put(PATH, json=payload))
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == INVALID
    assert commits == []
    await _nothing_written(maker)


# ── a change: ONE commit, audited twice; the same value again writes nothing ─

async def _audit(maker: Any) -> tuple[list[SettingsAuditLog], list[IdentityEvent]]:
    async with maker.session() as db:
        settings_rows = list((await db.execute(select(SettingsAuditLog))).scalars().all())
        events = list((await db.execute(select(IdentityEvent))).scalars().all())
    return settings_rows, events


async def test_a_change_is_one_commit_audited_in_both_trails(maker: Any, world: SimpleNamespace) -> None:
    commits: list[str] = []
    async with _client(maker, _jwt(world.admin), commits) as client:
        erase = await client.put(PATH, json={"action": "erase"})
        assert commits == ["commit"]
        again = await client.put(PATH, json={"action": "erase"})       # a double click: nothing more
        assert commits == ["commit"]
        read = await client.get(PATH)
    assert erase.status_code == 200 and again.status_code == 200, erase.text
    assert erase.json()["action"] == "erase" and erase.json()["updated_by_display"] == "Zqxwadmin"
    assert erase.json()["updated_at"] is not None and read.json() == again.json() == erase.json()
    async with maker.session() as db:
        row = await db.get(AppSetting, setting.APP_SETTINGS_KEY)
        assert row.value == {"action": "erase"} and row.updated_by == world.admin.id
    settings_rows, events = await _audit(maker)
    assert [(r.setting_key, r.action, r.actor_id, r.changed_fields) for r in settings_rows] == [
        ("scim_delete_action", "updated", world.admin.id, ["action"])]
    assert len(events) == 1
    event = events[0]
    assert event.event_type == IdentityEventType.SCIM_DELETE_ACTION_UPDATED.value
    assert (event.actor_id, event.actor_name, event.user_id) == (world.admin.id, "zqxwadmin", None)
    assert event.detail == {"before": "deactivate", "after": "erase"} and event.success is not False


async def test_switching_back_is_audited_again(maker: Any, world: SimpleNamespace) -> None:
    commits: list[str] = []
    async with _client(maker, _jwt(world.admin), commits) as client:
        await client.put(PATH, json={"action": "erase"})
        back = await client.put(PATH, json={"action": "deactivate"})
    assert back.status_code == 200 and back.json()["action"] == "deactivate" and commits == ["commit", "commit"]
    settings_rows, events = await _audit(maker)
    assert len(settings_rows) == 2
    assert sorted((e.detail["before"], e.detail["after"]) for e in events) == [
        ("deactivate", "erase"), ("erase", "deactivate")]


async def test_deactivate_with_no_row_is_unchanged_and_writes_nothing(maker: Any, world: SimpleNamespace) -> None:
    commits: list[str] = []
    async with _client(maker, _jwt(world.admin), commits) as client:
        response = await client.put(PATH, json={"action": "deactivate"})
    assert response.status_code == 200
    assert response.json() == {"action": "deactivate", "updated_at": None, "updated_by_display": None}
    assert commits == []
    await _nothing_written(maker)


async def test_an_unreadable_row_is_overwritten_by_erase(maker: Any, world: SimpleNamespace) -> None:
    await _store(maker, {"action": "obliterate"})
    async with maker.session() as db:
        assert await setting.set_action(db, "erase", world.admin) == ("deactivate", "erase")
        await db.commit()
        assert await setting.get_action(db) == "erase"


async def test_the_service_refuses_an_unknown_action(maker: Any, world: SimpleNamespace) -> None:
    async with maker.session() as db:
        with pytest.raises(ValueError):
            await setting.set_action(db, "obliterate", world.admin)
    await _nothing_written(maker)


def test_the_service_is_stage_only_and_names_no_one_in_its_logs() -> None:
    tree = ast.parse(SERVICE_PY.read_text(encoding="utf-8"))
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not {"commit", "rollback"} & calls
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                isinstance(node.func.value, ast.Name) and node.func.value.id == "_slog":
            assert {kw.arg for kw in node.keywords} <= {"before", "after", "value_type"}


def test_the_new_identity_event_types_fit_the_column() -> None:
    for name in ("SCIM_DELETE_ACTION_UPDATED", "SCIM_ERASURE_REFUSED", "SCIM_USER_ERASURE_STARTED"):
        assert IdentityEventType[name].value == name and len(name) <= 40
