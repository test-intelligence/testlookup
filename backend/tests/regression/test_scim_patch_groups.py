from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        ("groups", [{"value": "id-1", "display": "Admins"}, {"value": "QA"}], ["Admins", "QA"]),
        (None, {"groups": [{"display": "Developers"}]}, ["Developers"]),
        ("groups", [], []),
    ],
)
async def test_patch_group_replace_reaches_role_and_identity_sync(
    monkeypatch, path, value, expected
):
    from app.models.schemas import SCIMPatchOp, SCIMPatchRequest
    from app.routers import scim as router

    update = AsyncMock(return_value=_user())
    monkeypatch.setattr(router, "scim_update_user", update)
    payload = SCIMPatchRequest(
        Operations=[SCIMPatchOp(op="replace", path=path, value=value)]
    )
    request = SimpleNamespace(client=None, base_url="https://example.test/")
    token = SimpleNamespace(sso_config_id=uuid.uuid4())
    db = AsyncMock()

    await router.scim_patch(uuid.uuid4(), payload, request, token, db)

    assert update.await_args.kwargs["groups"] is None
    assert update.await_args.kwargs["group_operations"] == [("replace", expected)]


@pytest.mark.asyncio
async def test_unrelated_patch_leaves_groups_untouched(monkeypatch):
    from app.models.schemas import SCIMPatchOp, SCIMPatchRequest
    from app.routers import scim as router

    update = AsyncMock(return_value=_user())
    monkeypatch.setattr(router, "scim_update_user", update)
    payload = SCIMPatchRequest(
        Operations=[SCIMPatchOp(op="replace", path="active", value=False)]
    )
    request = SimpleNamespace(client=None, base_url="https://example.test/")
    token = SimpleNamespace(sso_config_id=uuid.uuid4())

    await router.scim_patch(uuid.uuid4(), payload, request, token, AsyncMock())

    assert update.await_args.kwargs["groups"] is None
    assert update.await_args.kwargs["group_operations"] is None
