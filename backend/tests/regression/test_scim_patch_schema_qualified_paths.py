import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


CORE_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def test_core_qualified_scalar_and_bulk_paths_canonicalize():
    from app.routers.scim import (
        _canonical_scim_patch_object,
        _canonical_scim_patch_path,
    )

    assert _canonical_scim_patch_path(f"{CORE_USER}:USERNAME") == "userName"
    assert _canonical_scim_patch_object(
        {f"{CORE_USER}:DisplayName": "Case Preserved"}
    ) == {"displayName": "Case Preserved"}


def test_core_qualified_group_filter_preserves_quoted_value():
    from app.routers.scim import _canonical_scim_patch_path, _scim_group_filter_name

    path = _canonical_scim_patch_path(f'{CORE_USER}:Groups[Value EQ "AdminCase"]')

    assert path == 'Groups[Value EQ "AdminCase"]'
    assert _scim_group_filter_name(path) == "AdminCase"


@pytest.mark.asyncio
async def test_core_qualified_path_reaches_update_service(monkeypatch):
    from app.models.schemas import SCIMPatchOp, SCIMPatchRequestPayload
    from app.routers import scim

    update = AsyncMock(side_effect=RuntimeError("stop after parsing"))
    monkeypatch.setattr(scim, "scim_update_user", update)
    payload = SCIMPatchRequestPayload(
        schemas=[PATCH_SCHEMA],
        Operations=[
            SCIMPatchOp(
                op="replace",
                path=f"{CORE_USER}:userName",
                value="QualifiedCase",
            )
        ],
    )

    with pytest.raises(RuntimeError, match="stop after parsing"):
        await scim.scim_patch(
            uuid.uuid4(),
            payload,
            MagicMock(client=None),
            SimpleNamespace(sso_config_id=None),
            AsyncMock(),
        )

    assert update.await_args.kwargs["username"] == "QualifiedCase"


@pytest.mark.asyncio
async def test_unsupported_extension_qualified_path_is_rejected_before_mutation(monkeypatch):
    from app.models.schemas import SCIMPatchOp, SCIMPatchRequestPayload
    from app.routers import scim

    update = AsyncMock()
    monkeypatch.setattr(scim, "scim_update_user", update)
    extension_path = "urn:example:params:scim:schemas:extension:2.0:User:department"
    payload = SCIMPatchRequestPayload(
        schemas=[PATCH_SCHEMA],
        Operations=[SCIMPatchOp(op="replace", path=extension_path, value="QA")],
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
    assert raised.value.detail == f"Unsupported PATCH path: {extension_path}"
    update.assert_not_awaited()
