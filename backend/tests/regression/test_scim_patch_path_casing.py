import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


@pytest.mark.asyncio
async def test_mixed_case_paths_reach_canonical_update_fields_without_changing_values(monkeypatch):
    from app.models.schemas import SCIMPatchOp, SCIMPatchRequestPayload
    from app.routers import scim

    user_id = uuid.uuid4()
    config_id = uuid.uuid4()
    user = SimpleNamespace(
        id=user_id,
        username="AliceCase",
        email="alice@example.test",
        full_name="DisplayCase",
        is_active=False,
        created_at=None,
        updated_at=None,
    )
    update = AsyncMock(return_value=user)
    monkeypatch.setattr(scim, "scim_update_user", update)
    monkeypatch.setattr(
        scim,
        "scim_identity_map",
        AsyncMock(return_value={user_id: SimpleNamespace(external_id="ExtCase", external_groups=[])}),
    )
    payload = SCIMPatchRequestPayload(
        schemas=[PATCH_SCHEMA],
        Operations=[
            SCIMPatchOp(op="replace", path="USERNAME", value="AliceCase"),
            SCIMPatchOp(op="replace", path="DisplayName", value="DisplayCase"),
            SCIMPatchOp(op="replace", path="ACTIVE", value=False),
            SCIMPatchOp(op="remove", path='Groups[Value EQ "AdminCase"]'),
        ],
    )

    await scim.scim_patch(
        user_id,
        payload,
        MagicMock(base_url="https://example.test/", client=None),
        SimpleNamespace(sso_config_id=config_id),
        AsyncMock(),
    )

    kwargs = update.await_args.kwargs
    assert kwargs["username"] == "AliceCase"
    assert kwargs["display_name"] == "DisplayCase"
    assert kwargs["active"] is False
    assert kwargs["group_operations"] == [("remove", [{"value": "AdminCase"}])]


@pytest.mark.asyncio
async def test_bulk_keys_are_case_insensitive(monkeypatch):
    from app.models.schemas import SCIMPatchOp, SCIMPatchRequestPayload
    from app.routers import scim

    update = AsyncMock()
    monkeypatch.setattr(scim, "scim_update_user", update)
    payload = SCIMPatchRequestPayload(
        schemas=[PATCH_SCHEMA],
        Operations=[
            SCIMPatchOp(
                op="replace",
                value={"USERNAME": "AliceCase", "displayNAME": "DisplayCase"},
            )
        ],
    )

    with pytest.raises(StopAsyncIteration):
        update.side_effect = StopAsyncIteration
        await scim.scim_patch(
            uuid.uuid4(),
            payload,
            MagicMock(client=None),
            SimpleNamespace(sso_config_id=None),
            AsyncMock(),
        )

    assert update.await_args.kwargs["username"] == "AliceCase"
    assert update.await_args.kwargs["display_name"] == "DisplayCase"


@pytest.mark.asyncio
async def test_duplicate_bulk_aliases_are_rejected_before_mutation(monkeypatch):
    from app.models.schemas import SCIMPatchOp, SCIMPatchRequestPayload
    from app.routers import scim

    update = AsyncMock()
    monkeypatch.setattr(scim, "scim_update_user", update)
    payload = SCIMPatchRequestPayload(
        schemas=[PATCH_SCHEMA],
        Operations=[
            SCIMPatchOp(
                op="replace",
                value={"userName": "alice", "USERNAME": "other"},
            )
        ],
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
    assert raised.value.detail == "Duplicate PATCH attribute: userName"
    update.assert_not_awaited()
