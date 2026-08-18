import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"


def test_omitted_primary_defaults_false_and_first_value_is_selected():
    from app.models.schemas import SCIMEmail
    from app.routers.scim import _scim_patch_email

    assert SCIMEmail(value="first@example.test").primary is False
    assert _scim_patch_email(
        [
            {"value": "first@example.test"},
            {"value": "second@example.test"},
        ]
    ) == "first@example.test"


def test_one_explicit_primary_wins_regardless_of_position():
    from app.routers.scim import _scim_patch_email

    assert _scim_patch_email(
        [
            {"value": "first@example.test"},
            {"value": "selected@example.test", "primary": True},
            {"value": "last@example.test"},
        ]
    ) == "selected@example.test"


def test_public_user_payload_rejects_multiple_primary_emails():
    from app.models.schemas import SCIMUserRequest

    with pytest.raises(ValidationError, match="at most one primary"):
        SCIMUserRequest(
            schemas=[USER_SCHEMA],
            userName="alice",
            emails=[
                {"value": "first@example.test", "primary": True},
                {"value": "second@example.test", "primary": True},
            ],
        )


@pytest.mark.asyncio
async def test_create_defensively_rejects_multiple_primaries_before_mutation(monkeypatch):
    from app.models.schemas import SCIMUserResource
    from app.routers import scim

    create = AsyncMock()
    monkeypatch.setattr(scim, "scim_create_user", create)
    payload = SCIMUserResource(
        userName="alice",
        emails=[
            {"value": "first@example.test", "primary": True},
            {"value": "second@example.test", "primary": True},
        ],
    )

    with pytest.raises(HTTPException) as raised:
        await scim.scim_create(
            payload,
            MagicMock(client=None),
            SimpleNamespace(sso_config_id=None),
            AsyncMock(),
        )

    assert raised.value.status_code == 400
    assert raised.value.detail == "emails must contain valid values and at most one primary"
    create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True])
async def test_patch_rejects_multiple_primaries_before_mutation(monkeypatch, bulk):
    from app.models.schemas import SCIMPatchRequestPayload
    from app.routers import scim

    update = AsyncMock()
    monkeypatch.setattr(scim, "scim_update_user", update)
    emails = [
        {"value": "first@example.test", "primary": True},
        {"value": "second@example.test", "primary": True},
    ]
    operation = (
        {"op": "replace", "value": {"emails": emails}}
        if bulk
        else {"op": "replace", "path": "emails", "value": emails}
    )
    payload = SCIMPatchRequestPayload(
        schemas=[PATCH_SCHEMA],
        Operations=[operation],
    )

    with pytest.raises(HTTPException) as raised:
        await scim.scim_patch(
            uuid.uuid4(),
            payload,
            MagicMock(client=None),
            SimpleNamespace(sso_config_id=uuid.uuid4()),
            AsyncMock(),
        )

    assert raised.value.status_code == 400
    assert raised.value.detail == "emails must contain a valid value"
    update.assert_not_awaited()
