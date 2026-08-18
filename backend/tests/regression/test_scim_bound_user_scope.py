from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _page_result(users):
    result = MagicMock()
    result.scalars.return_value.all.return_value = users
    return result


@pytest.mark.asyncio
async def test_bound_list_scopes_count_and_page_to_federated_identity():
    from app.services.scim_service import scim_list_users

    config_id = uuid.uuid4()
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_scalar_result(1), _page_result([])])

    await scim_list_users(db, sso_config_id=config_id)

    for call in db.execute.await_args_list:
        compiled = call.args[0].compile()
        assert "federated_identities.sso_config_id" in str(compiled)
        assert config_id in compiled.params.values()


@pytest.mark.asyncio
async def test_bound_get_scopes_lookup_but_unbound_get_remains_global():
    from app.services.scim_service import scim_get_user

    config_id = uuid.uuid4()
    db = MagicMock()
    db.execute = AsyncMock(return_value=_scalar_result(None))

    await scim_get_user(db, uuid.uuid4(), sso_config_id=config_id)
    bound_query = db.execute.await_args.args[0].compile()
    assert "federated_identities.sso_config_id" in str(bound_query)
    assert config_id in bound_query.params.values()

    await scim_get_user(db, uuid.uuid4())
    unbound_query = db.execute.await_args.args[0].compile()
    assert "federated_identities" not in str(unbound_query)


@pytest.mark.asyncio
async def test_bound_update_rejects_out_of_scope_user_before_mutation_or_audit():
    from app.services.scim_service import SCIMUserNotFoundError, scim_update_user

    config_id = uuid.uuid4()
    db = MagicMock()
    db.execute = AsyncMock(return_value=_scalar_result(None))

    with pytest.raises(SCIMUserNotFoundError):
        await scim_update_user(db, uuid.uuid4(), active=False, sso_config_id=config_id)

    db.add.assert_not_called()
    query = db.execute.await_args.args[0].compile()
    assert "federated_identities.sso_config_id" in str(query)
    assert config_id in query.params.values()


@pytest.mark.asyncio
async def test_bound_create_requires_external_id_before_service_call(monkeypatch):
    from app.models.schemas import SCIMUserResource
    from app.routers import scim as router

    create = AsyncMock()
    monkeypatch.setattr(router, "scim_create_user", create)
    payload = SCIMUserResource(userName="alice", emails=[{"value": "a@example.com", "primary": True}])
    request = SimpleNamespace(client=None)
    token = SimpleNamespace(sso_config_id=uuid.uuid4())

    with pytest.raises(HTTPException) as exc:
        await router.scim_create(payload, request, token, AsyncMock())

    assert exc.value.status_code == 400
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_maps_scoped_user_denial_to_not_found(monkeypatch):
    from app.models.schemas import SCIMPatchOp, SCIMPatchRequest
    from app.routers import scim as router
    from app.services.scim_service import SCIMUserNotFoundError

    monkeypatch.setattr(
        router,
        "scim_update_user",
        AsyncMock(side_effect=SCIMUserNotFoundError("User not found")),
    )
    payload = SCIMPatchRequest(
        Operations=[SCIMPatchOp(op="replace", path="active", value=False)]
    )
    request = SimpleNamespace(client=None)
    token = SimpleNamespace(sso_config_id=uuid.uuid4())

    with pytest.raises(HTTPException) as exc:
        await router.scim_patch(uuid.uuid4(), payload, request, token, AsyncMock())

    assert exc.value.status_code == 404
