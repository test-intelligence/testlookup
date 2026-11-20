"""A SCIM token manages only the accounts provisioning owns, and never locks the last administrator out.

The rules pinned here (the real SCIM router over an in-memory SQLite world; no token is ever real):

* A token that is NOT bound to an identity provider reaches only accounts that SCIM created (a
  ``SCIM_USER_CREATED`` identity event names the user) or that have a federated link. Any other user id answers
  404 on list, GET, PUT, PATCH and DELETE, exactly as an out-of-directory id does for a bound token.
* No SCIM token, bound or not, deactivates or deletes the last active administrator, and none lowers that
  account's role through a group change: 409 with the SCIM error envelope, and the account stays as it was.
* A userName or e-mail change needs a token bound to the directory the account is linked to. Every account can
  ask for a password-reset mail, so the address is the key to the account; an unbound token gets 403.
* A group's member list is read and written inside the same scope: a token neither adds nor removes a user it
  cannot see.
* The legitimate paths still work: an unbound token updates and deactivates a user it created, and one of two
  administrators can be deactivated.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.scim_errors import scim_http_exception_handler
from app.db.postgres import get_db
from app.models.postgres import (
    AppSetting,
    Base,
    FederatedIdentity,
    IdentityEvent,
    IdentityEventType,
    SCIMGroup,
    SCIMGroupMember,
    SSOConfiguration,
    User,
    UserRole,
)
from app.routers import scim as scim_routes
from app.services import scim_delete_setting
from tests.test_subject_erasure_service import _user
from tests.test_subject_erasure_service import maker as _sqlite_maker
from tests.test_subject_erasure_service import world as _sqlite_world

pytestmark = pytest.mark.regression

_maker = pytest.fixture(name="_maker")(_sqlite_maker.__wrapped__)  # type: ignore[attr-defined]
world = pytest.fixture(name="world")(_sqlite_world.__wrapped__)  # type: ignore[attr-defined]

USERS = "/api/v1/scim/v2/Users"
GROUPS = "/api/v1/scim/v2/Groups"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
UNBOUND = SimpleNamespace(sso_config_id=None, id=uuid.UUID("5c1a0000-0000-4000-8000-00000000f017"))
_EXTRA_TABLES = ("sso_configurations", "scim_groups", "scim_group_members", "scim_group_mappings",
                 "projects", "project_members")


@pytest.fixture
async def maker(_maker: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The erasure tests' SQLite world plus the SSO and SCIM-group tables these rules read."""
    for name in _EXTRA_TABLES:
        if name in _maker.created:
            continue
        table = Base.metadata.tables[name]
        for column in table.columns:
            rendered = str(getattr(column.server_default, "arg", "")) if column.server_default is not None else ""
            if "::" in rendered or "gen_random" in rendered:
                monkeypatch.setattr(column, "server_default", None)
        async with _maker.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[table])
        _maker.created.add(name)

    async def _no_cache(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(scim_routes, "invalidate_membership_cache", _no_cache)
    monkeypatch.setattr("app.services.scim_group_service.invalidate", _no_cache)
    monkeypatch.setattr("app.core.token_revocation.revoke_all_user_tokens", _no_cache)
    monkeypatch.setattr(scim_routes, "_dispatch_erasure", lambda job_id: None)
    return _maker


def _client(maker: Any, token: Any) -> AsyncClient:
    app = FastAPI()
    app.include_router(scim_routes.router)
    app.add_exception_handler(StarletteHTTPException, scim_http_exception_handler)

    async def _db() -> Any:
        async with maker.session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[scim_routes.verify_scim_bearer] = lambda: token
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _call(maker: Any, token: Any, method: str, url: str, **kwargs: Any) -> Any:
    async with _client(maker, token) as client:
        return await client.request(method, url, **kwargs)


def _patch(*operations: dict[str, Any]) -> dict[str, Any]:
    return {"schemas": [PATCH_SCHEMA], "Operations": list(operations)}


def _replace(path: str, value: Any) -> dict[str, Any]:
    return {"op": "replace", "path": path, "value": value}


def _emails(address: str) -> list[dict[str, Any]]:
    return [{"value": address, "primary": True}]


def _body(name: str, **extra: Any) -> dict[str, Any]:
    return {"schemas": [USER_SCHEMA], "userName": name, "emails": _emails(f"{name}@example.org"), **extra}


async def _provision(maker: Any, name: str, token: Any = UNBOUND, **extra: Any) -> uuid.UUID:
    """A user created the way an identity provider does it: ``POST /Users``."""
    created = await _call(maker, token, "POST", USERS, json=_body(name, **extra))
    assert created.status_code == 201, created.text
    return uuid.UUID(created.json()["id"])


async def _row(maker: Any, user_id: uuid.UUID) -> User:
    async with maker.session() as db:
        row = await db.get(User, user_id)
        assert row is not None
        return row


async def _set(maker: Any, user_id: uuid.UUID, **values: Any) -> None:
    async with maker.session() as db:
        row = await db.get(User, user_id)
        for key, value in values.items():
            setattr(row, key, value)
        await db.commit()


async def _link(maker: Any, user_id: uuid.UUID, config_id: uuid.UUID) -> None:
    async with maker.session() as db:
        db.add(FederatedIdentity(id=uuid.uuid4(), user_id=user_id, sso_config_id=config_id,
                                 external_id=f"ext-{user_id.hex[:8]}", external_groups=[]))
        await db.commit()


async def _erase_mode(maker: Any) -> None:
    async with maker.session() as db:
        db.add(AppSetting(key=scim_delete_setting.APP_SETTINGS_KEY, value={"action": "erase"}))
        await db.commit()


def _assert_scim_error(response: Any, status: int) -> str:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["schemas"] == [ERROR_SCHEMA] and body["status"] == str(status)
    return str(body["detail"])


# ── an unbound token reaches only SCIM-managed accounts ──────────────────────

async def test_unbound_token_cannot_change_the_email_of_a_local_administrator(maker: Any, world: Any) -> None:
    admin = world.admin
    refused = await _call(maker, UNBOUND, "PATCH", f"{USERS}/{admin.id}",
                          json=_patch(_replace("emails", _emails("taken.over@example.org"))))
    assert _assert_scim_error(refused, 404) == f"User {admin.id} not found"
    assert (await _row(maker, admin.id)).email == admin.email
    async with maker.session() as db:   # nothing was recorded against the account either
        assert (await db.execute(select(IdentityEvent).where(IdentityEvent.user_id == admin.id))).first() is None


async def test_every_user_route_answers_404_for_an_account_scim_does_not_manage(maker: Any, world: Any) -> None:
    local = world.subject
    url = f"{USERS}/{local.id}"
    assert (await _call(maker, UNBOUND, "GET", url)).status_code == 404
    assert (await _call(maker, UNBOUND, "PUT", url, json=_body("zqxwrenamed"))).status_code == 404
    assert (await _call(maker, UNBOUND, "PATCH", url, json=_patch(_replace("active", False)))).status_code == 404
    assert (await _call(maker, UNBOUND, "PATCH", url,
                        json=_patch(_replace("displayName", "Someone Else")))).status_code == 404
    assert (await _call(maker, UNBOUND, "DELETE", url)).status_code == 404
    await _erase_mode(maker)
    assert (await _call(maker, UNBOUND, "DELETE", url)).status_code == 404
    row = await _row(maker, local.id)
    assert (row.username, row.email, row.full_name, row.is_active) == (
        local.username, local.email, local.full_name, True)


async def test_the_list_holds_only_scim_created_and_linked_accounts(maker: Any, world: Any) -> None:
    created = await _provision(maker, "zqxwmanaged")
    await _link(maker, world.bot.id, uuid.uuid4())           # a local account that signs in through a directory
    listed = await _call(maker, UNBOUND, "GET", USERS)
    assert listed.status_code == 200
    body = listed.json()
    assert {item["id"] for item in body["Resources"]} == {str(created), str(world.bot.id)}
    assert body["totalResults"] == 2
    by_name = await _call(maker, UNBOUND, "GET", USERS, params={"filter": f'userName eq "{world.admin.username}"'})
    assert by_name.json()["totalResults"] == 0 and by_name.json()["Resources"] == []
    assert (await _call(maker, UNBOUND, "GET", f"{USERS}/{world.bot.id}")).status_code == 200


# ── the legitimate path: an unbound token still manages what it created ──────

async def test_unbound_token_updates_and_deactivates_a_user_it_created(maker: Any, world: Any) -> None:
    user_id = await _provision(maker, "zqxwmanaged")
    url = f"{USERS}/{user_id}"
    assert (await _call(maker, UNBOUND, "GET", url)).json()["userName"] == "zqxwmanaged"
    renamed = await _call(maker, UNBOUND, "PATCH", url, json=_patch(_replace("displayName", "Managed Person")))
    assert renamed.status_code == 200 and renamed.json()["displayName"] == "Managed Person"
    # a full replace that repeats the userName and the address is not an identity change
    replaced = await _call(maker, UNBOUND, "PUT", url, json=_body("zqxwmanaged", displayName="Managed Again"))
    assert replaced.status_code == 200 and replaced.json()["displayName"] == "Managed Again"
    off = await _call(maker, UNBOUND, "PATCH", url, json=_patch(_replace("active", False)))
    assert off.status_code == 200 and off.json()["active"] is False
    on = await _call(maker, UNBOUND, "PATCH", url, json=_patch(_replace("active", True)))
    assert on.status_code == 200 and on.json()["active"] is True
    assert (await _call(maker, UNBOUND, "DELETE", url)).status_code == 204
    assert (await _row(maker, user_id)).is_active is False


# ── the last active administrator ────────────────────────────────────────────

async def _only_admin(maker: Any, world: Any, token: Any) -> uuid.UUID:
    """The world's ONE active administrator, as an account ``token`` manages."""
    if token.sso_config_id is not None:
        await _link(maker, world.admin.id, token.sso_config_id)
        return world.admin.id
    user_id = await _provision(maker, "zqxwboss")
    await _set(maker, user_id, role=UserRole.ADMIN.value)
    await _set(maker, world.admin.id, is_active=False)
    return user_id


@pytest.mark.parametrize("bound", [False, True], ids=["unbound", "bound"])
@pytest.mark.parametrize("mode", ["deactivate", "erase"])
async def test_no_token_deactivates_or_deletes_the_last_active_administrator(
    maker: Any, world: Any, bound: bool, mode: str,
) -> None:
    token = SimpleNamespace(sso_config_id=uuid.uuid4(), id=uuid.uuid4()) if bound else UNBOUND
    admin_id = await _only_admin(maker, world, token)
    if mode == "erase":
        await _erase_mode(maker)
    url = f"{USERS}/{admin_id}"
    patched = await _call(maker, token, "PATCH", url, json=_patch(_replace("active", False)))
    detail = _assert_scim_error(patched, 409)
    assert "last active administrator" in detail and "another administrator" in detail
    name = (await _row(maker, admin_id)).username
    replaced = await _call(maker, token, "PUT", url, json=_body(name, active=False, externalId="ext-put"))
    _assert_scim_error(replaced, 409)
    deleted = await _call(maker, token, "DELETE", url)
    _assert_scim_error(deleted, 409)
    row = await _row(maker, admin_id)
    assert row.is_active is True and row.role == UserRole.ADMIN.value
    async with maker.session() as db:   # a refused call records no deactivation
        kinds = (await db.execute(select(IdentityEvent.event_type).where(IdentityEvent.user_id == admin_id))).scalars()
        assert IdentityEventType.SCIM_USER_DEACTIVATED.value not in set(kinds)


async def test_one_of_two_administrators_can_be_deactivated(maker: Any, world: Any) -> None:
    user_id = await _provision(maker, "zqxwboss")
    await _set(maker, user_id, role=UserRole.ADMIN.value)     # world.admin is the second active administrator
    off = await _call(maker, UNBOUND, "PATCH", f"{USERS}/{user_id}", json=_patch(_replace("active", False)))
    assert off.status_code == 200 and off.json()["active"] is False
    assert (await _row(maker, user_id)).is_active is False
    assert (await _row(maker, world.admin.id)).is_active is True


async def test_a_group_change_cannot_lower_the_last_administrators_role(maker: Any, world: Any) -> None:
    config_id = uuid.uuid4()
    async with maker.session() as db:
        db.add(SSOConfiguration(id=config_id, display_name="Directory", is_active=True, provider_type="OIDC",
                                oidc_issuer="https://idp.example.org", oidc_client_id="zqxw-client",
                                role_mapping={"admins": "ADMIN"}, default_role=UserRole.VIEWER.value))
        await db.commit()
    token = SimpleNamespace(sso_config_id=config_id, id=uuid.uuid4())
    await _link(maker, world.admin.id, config_id)
    url = f"{USERS}/{world.admin.id}"
    refused = await _call(maker, token, "PATCH", url, json=_patch(_replace("groups", [])))
    detail = _assert_scim_error(refused, 409)
    assert "last active administrator" in detail
    assert (await _row(maker, world.admin.id)).role == UserRole.ADMIN.value
    # with a second active administrator the same change goes through
    async with maker.session() as db:
        db.add(_user(UserRole.ADMIN, "zqxwsecond"))
        await db.commit()
    allowed = await _call(maker, token, "PATCH", url, json=_patch(_replace("groups", [])))
    assert allowed.status_code == 200, allowed.text
    assert (await _row(maker, world.admin.id)).role == UserRole.VIEWER.value


# ── userName and e-mail: only the directory the account is linked to ─────────

@pytest.mark.parametrize("change", [
    _replace("emails", _emails("taken.over@example.org")),
    _replace("userName", "zqxwother"),
], ids=["email", "userName"])
async def test_unbound_token_cannot_change_a_managed_accounts_sign_in_identity(
    maker: Any, world: Any, change: dict[str, Any],
) -> None:
    user_id = await _provision(maker, "zqxwmanaged")
    url = f"{USERS}/{user_id}"
    refused = await _call(maker, UNBOUND, "PATCH", url, json=_patch(change))
    detail = _assert_scim_error(refused, 403)
    assert "userName or e-mail" in detail and "bound to" in detail
    replaced = await _call(maker, UNBOUND, "PUT", url, json={
        "schemas": [USER_SCHEMA], "userName": "zqxwother", "emails": _emails("taken.over@example.org")})
    _assert_scim_error(replaced, 403)
    row = await _row(maker, user_id)
    assert (row.username, row.email) == ("zqxwmanaged", "zqxwmanaged@example.org")


async def test_unbound_token_cannot_change_the_email_of_a_linked_account(maker: Any, world: Any) -> None:
    await _link(maker, world.subject.id, uuid.uuid4())
    refused = await _call(maker, UNBOUND, "PATCH", f"{USERS}/{world.subject.id}",
                          json=_patch(_replace("emails", _emails("taken.over@example.org"))))
    _assert_scim_error(refused, 403)
    assert (await _row(maker, world.subject.id)).email == world.subject.email


async def test_a_bound_token_changes_the_identity_of_an_account_linked_to_its_directory(
    maker: Any, world: Any,
) -> None:
    config_id = uuid.uuid4()
    token = SimpleNamespace(sso_config_id=config_id, id=uuid.uuid4())
    await _link(maker, world.subject.id, config_id)
    changed = await _call(maker, token, "PATCH", f"{USERS}/{world.subject.id}", json=_patch(
        _replace("emails", _emails("new.address@example.org")), _replace("userName", "zqxwnewname")))
    assert changed.status_code == 200, changed.text
    row = await _row(maker, world.subject.id)
    assert (row.username, row.email) == ("zqxwnewname", "new.address@example.org")


# ── groups: a token neither adds, removes nor lists a user it cannot see ─────

async def _group(maker: Any, name: str, members: list[uuid.UUID]) -> uuid.UUID:
    group_id = uuid.uuid4()
    async with maker.session() as db:
        db.add(SCIMGroup(id=group_id, display_name=name, version=1))
        await db.flush()
        for member in members:
            db.add(SCIMGroupMember(group_id=group_id, user_id=member))
        await db.commit()
    return group_id


async def _members(maker: Any, group_id: uuid.UUID) -> set[uuid.UUID]:
    async with maker.session() as db:
        return set((await db.execute(
            select(SCIMGroupMember.user_id).where(SCIMGroupMember.group_id == group_id))).scalars().all())


async def test_a_group_cannot_take_a_member_the_token_does_not_manage(maker: Any, world: Any) -> None:
    managed = await _provision(maker, "zqxwmanaged")
    group_id = await _group(maker, "zqxw-qa", [])
    local = {"value": str(world.admin.id)}
    for response in [
        await _call(maker, UNBOUND, "POST", GROUPS, json={"displayName": "zqxw-new", "members": [local]}),
        await _call(maker, UNBOUND, "PUT", f"{GROUPS}/{group_id}", json={"displayName": "zqxw-qa", "members": [local]}),
        await _call(maker, UNBOUND, "PATCH", f"{GROUPS}/{group_id}",
                    json={"Operations": [{"op": "add", "path": "members", "value": [local]}]}),
    ]:
        assert _assert_scim_error(response, 400) == "Unknown member id(s)"
    assert await _members(maker, group_id) == set()
    added = await _call(maker, UNBOUND, "PATCH", f"{GROUPS}/{group_id}",
                        json={"Operations": [{"op": "add", "path": "members", "value": [{"value": str(managed)}]}]})
    assert added.status_code == 200, added.text
    assert await _members(maker, group_id) == {managed}


async def test_a_group_write_leaves_members_the_token_does_not_manage(maker: Any, world: Any) -> None:
    managed = await _provision(maker, "zqxwmanaged")
    local = world.subject.id
    group_id = await _group(maker, "zqxw-qa", [managed, local])
    url = f"{GROUPS}/{group_id}"
    shown = await _call(maker, UNBOUND, "GET", url)
    assert [m["value"] for m in shown.json()["members"]] == [str(managed)]      # the local member is not listed
    listed = await _call(maker, UNBOUND, "GET", GROUPS)
    assert [m["value"] for m in listed.json()["Resources"][0]["members"]] == [str(managed)]
    for body in [
        {"Operations": [{"op": "remove", "path": f'members[value eq "{local}"]'}]},
        {"Operations": [{"op": "remove", "path": "members"}]},
        {"Operations": [{"op": "replace", "path": "members", "value": []}]},
    ]:
        assert (await _call(maker, UNBOUND, "PATCH", url, json=body)).status_code == 200
        assert local in await _members(maker, group_id)
    replaced = await _call(maker, UNBOUND, "PUT", url, json={"displayName": "zqxw-qa", "members": []})
    assert replaced.status_code == 200 and replaced.json()["members"] == []
    assert await _members(maker, group_id) == {local}                            # the managed member did go
