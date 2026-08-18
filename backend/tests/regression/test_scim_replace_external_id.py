import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_update_user_persists_bound_external_id_and_audits_change(monkeypatch):
    from app.services import scim_service

    user_id = uuid.uuid4()
    config_id = uuid.uuid4()
    user = SimpleNamespace(
        id=user_id,
        username="alice",
        email="alice@example.test",
        full_name="Alice",
        is_active=True,
        role="viewer",
    )
    identity = SimpleNamespace(
        external_id="old-id",
        external_groups=[],
        external_email="alice@example.test",
        external_display_name="Alice",
    )
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    identity_result = MagicMock()
    identity_result.scalar_one_or_none.return_value = identity
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[user_result, identity_result])
    audit = AsyncMock()
    monkeypatch.setattr(scim_service, "log_identity_event", audit)

    result = await scim_service.scim_update_user(
        db,
        user_id,
        external_id="new-id",
        sso_config_id=config_id,
    )

    assert result is user
    assert identity.external_id == "new-id"
    assert audit.await_args.kwargs["detail"]["external_id"] == {
        "old": "old-id",
        "new": "new-id",
    }


@pytest.mark.asyncio
async def test_bound_replace_rejects_missing_external_id_before_mutation():
    from app.models.schemas import SCIMUserRequest
    from app.routers.scim import scim_replace

    payload = SCIMUserRequest(
        schemas=["urn:ietf:params:scim:schemas:core:2.0:User"],
        userName="alice",
    )
    db = AsyncMock()

    with pytest.raises(HTTPException) as raised:
        await scim_replace(
            uuid.uuid4(),
            payload,
            MagicMock(),
            SimpleNamespace(sso_config_id=uuid.uuid4()),
            db,
        )

    assert raised.value.status_code == 400
    assert raised.value.detail == "externalId is required for an IdP-bound SCIM token"
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()


def test_replace_threads_external_id_into_update_service():
    from app.routers.scim import scim_replace

    source = inspect.getsource(scim_replace)
    assert "external_id=payload.externalId" in source


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True])
async def test_bound_patch_threads_external_id_into_update_service(monkeypatch, bulk):
    from app.models.schemas import SCIMPatchOp, SCIMPatchRequestPayload
    from app.routers import scim

    user_id = uuid.uuid4()
    config_id = uuid.uuid4()
    user = SimpleNamespace(
        id=user_id,
        username="alice",
        email="alice@example.test",
        full_name="Alice",
        is_active=True,
        created_at=None,
        updated_at=None,
    )
    update = AsyncMock(return_value=user)
    monkeypatch.setattr(scim, "scim_update_user", update)
    monkeypatch.setattr(
        scim,
        "scim_identity_map",
        AsyncMock(
            return_value={
                user_id: SimpleNamespace(external_id="new-id", external_groups=[]),
            }
        ),
    )
    operation = (
        SCIMPatchOp(op="replace", value={"externalId": "new-id"})
        if bulk
        else SCIMPatchOp(op="replace", path="externalId", value="new-id")
    )
    payload = SCIMPatchRequestPayload(
        schemas=["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        Operations=[operation],
    )

    response = await scim.scim_patch(
        user_id,
        payload,
        MagicMock(base_url="https://example.test/", client=None),
        SimpleNamespace(sso_config_id=config_id),
        AsyncMock(),
    )

    assert update.await_args.kwargs["external_id"] == "new-id"
    assert response.externalId == "new-id"


@pytest.mark.asyncio
async def test_unbound_patch_rejects_external_id_before_mutation(monkeypatch):
    from app.models.schemas import SCIMPatchOp, SCIMPatchRequestPayload
    from app.routers import scim

    update = AsyncMock()
    monkeypatch.setattr(scim, "scim_update_user", update)
    payload = SCIMPatchRequestPayload(
        schemas=["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        Operations=[SCIMPatchOp(op="replace", path="externalId", value="new-id")],
    )

    with pytest.raises(HTTPException) as raised:
        await scim.scim_patch(
            uuid.uuid4(),
            payload,
            MagicMock(client=None),
            SimpleNamespace(sso_config_id=None),
            AsyncMock(),
        )

    assert raised.value.status_code == 400
    assert raised.value.detail == "externalId updates require an IdP-bound SCIM token"
    update.assert_not_awaited()
