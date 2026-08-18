from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operations",
    [
        [{"op": "move", "path": "userName", "value": "alice"}],
        [{"op": "replace", "path": "title", "value": "Engineer"}],
        [{"op": "add", "path": "userName", "value": "alice"}],
        [{"op": "replace", "path": "active", "value": "false"}],
        [{"op": "replace", "path": "emails", "value": []}],
        [{"op": "replace", "path": "groups", "value": [123]}],
        [{"op": "replace", "value": {"unknown": "value"}}],
        [{"op": "replace", "value": "not-an-object"}],
    ],
)
async def test_invalid_patch_is_rejected_before_side_effects(monkeypatch, operations):
    from app.models.schemas import SCIMPatchRequest
    from app.routers import scim as router

    update = AsyncMock()
    identity_map = AsyncMock()
    monkeypatch.setattr(router, "scim_update_user", update)
    monkeypatch.setattr(router, "scim_identity_map", identity_map)
    payload = SCIMPatchRequest(Operations=operations)
    request = SimpleNamespace(client=None, base_url="https://example.test/")
    token = SimpleNamespace(sso_config_id=uuid.uuid4())
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await router.scim_patch(uuid.uuid4(), payload, request, token, db)

    assert exc.value.status_code == 400
    update.assert_not_awaited()
    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()
    identity_map.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_false_and_first_email_are_not_coerced_or_ignored(monkeypatch):
    from app.models.schemas import SCIMPatchRequest
    from app.routers import scim as router

    user = SimpleNamespace(
        id=uuid.uuid4(),
        username="alice",
        email="alice@example.com",
        full_name="Alice",
        is_active=False,
        created_at=None,
        updated_at=None,
    )
    update = AsyncMock(return_value=user)
    monkeypatch.setattr(router, "scim_update_user", update)
    monkeypatch.setattr(router, "scim_identity_map", AsyncMock(return_value={}))
    payload = SCIMPatchRequest(
        Operations=[
            {"op": "replace", "path": "active", "value": False},
            {
                "op": "replace",
                "path": "emails",
                "value": [{"value": "first@example.com"}],
            },
        ]
    )
    request = SimpleNamespace(client=None, base_url="https://example.test/")
    token = SimpleNamespace(sso_config_id=uuid.uuid4())

    await router.scim_patch(uuid.uuid4(), payload, request, token, AsyncMock())

    assert update.await_args.kwargs["active"] is False
    assert update.await_args.kwargs["email"] == "first@example.com"
