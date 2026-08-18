from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _user():
    return SimpleNamespace(
        id=uuid.uuid4(),
        username="alice",
        email="alice@example.com",
        full_name="Alice",
        is_active=True,
        created_at=None,
        updated_at=None,
    )


@pytest.mark.asyncio
async def test_identity_map_batches_bound_users_in_one_query():
    from app.services.scim_service import scim_identity_map

    config_id = uuid.uuid4()
    first = SimpleNamespace(user_id=uuid.uuid4())
    second = SimpleNamespace(user_id=uuid.uuid4())
    result = MagicMock()
    result.scalars.return_value.all.return_value = [first, second]
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    identities = await scim_identity_map(
        db,
        [first.user_id, second.user_id],
        config_id,
    )

    assert identities == {first.user_id: first, second.user_id: second}
    db.execute.assert_awaited_once()
    compiled = db.execute.await_args.args[0].compile()
    assert config_id in compiled.params.values()


@pytest.mark.asyncio
async def test_unbound_identity_map_omits_ambiguous_metadata_without_query():
    from app.services.scim_service import scim_identity_map

    db = MagicMock()
    db.execute = AsyncMock()

    assert await scim_identity_map(db, [uuid.uuid4()], None) == {}
    db.execute.assert_not_awaited()


def test_scim_serializer_returns_external_id_and_normalized_groups():
    from app.services.scim_service import user_to_scim_resource

    user = _user()
    identity = SimpleNamespace(
        external_id="external-alice",
        external_groups=[
            "Legacy",
            {"value": "group-admin-id", "display": "Administrators"},
        ],
    )

    resource = user_to_scim_resource(user, federated_identity=identity)

    assert resource["externalId"] == "external-alice"
    assert resource["groups"] == [
        {"value": "Legacy"},
        {"value": "group-admin-id", "display": "Administrators"},
    ]


@pytest.mark.asyncio
async def test_list_uses_one_identity_batch_and_returns_reconciliation_fields(monkeypatch):
    from app.routers import scim as router

    user = _user()
    identity = SimpleNamespace(
        external_id="external-alice",
        external_groups=[{"value": "group-1", "display": "QA"}],
    )
    list_users = AsyncMock(return_value=([user], 1))
    identity_map = AsyncMock(return_value={user.id: identity})
    monkeypatch.setattr(router, "scim_list_users", list_users)
    monkeypatch.setattr(router, "scim_identity_map", identity_map)
    request = SimpleNamespace(base_url="https://example.test/")
    token = SimpleNamespace(sso_config_id=uuid.uuid4())

    response = await router.scim_list(request, 1, 100, None, token, AsyncMock())

    assert response.Resources[0].externalId == "external-alice"
    assert response.Resources[0].groups[0].value == "group-1"
    identity_map.assert_awaited_once()
    assert identity_map.await_args.args[1] == [user.id]
