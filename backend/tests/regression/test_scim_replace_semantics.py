import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"


@pytest.mark.asyncio
async def test_replace_requires_application_email_before_mutation():
    from app.models.schemas import SCIMUserRequest
    from app.routers.scim import scim_replace

    db = AsyncMock()
    payload = SCIMUserRequest(
        schemas=[USER_SCHEMA],
        userName="alice",
        externalId="idp-alice",
    )

    with pytest.raises(HTTPException) as raised:
        await scim_replace(
            uuid.uuid4(),
            payload,
            MagicMock(),
            SimpleNamespace(sso_config_id=uuid.uuid4()),
            db,
        )

    assert raised.value.status_code == 400
    assert raised.value.detail == "At least one email is required"
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_replace_clears_omitted_display_name_in_user_and_identity(monkeypatch):
    from app.services import scim_service

    user_id = uuid.uuid4()
    config_id = uuid.uuid4()
    user = SimpleNamespace(
        id=user_id,
        username="alice",
        email="alice@example.test",
        full_name="Stale Name",
        is_active=True,
        role="viewer",
    )
    identity = SimpleNamespace(
        external_id="idp-alice",
        external_groups=[],
        external_email="alice@example.test",
        external_display_name="Stale Name",
    )
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    identity_result = MagicMock()
    identity_result.scalar_one_or_none.return_value = identity
    duplicate_result = MagicMock()
    duplicate_result.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[user_result, duplicate_result, identity_result])
    monkeypatch.setattr(scim_service, "log_identity_event", AsyncMock())

    await scim_service.scim_update_user(
        db,
        user_id,
        display_name=None,
        external_id="idp-alice",
        sso_config_id=config_id,
        full_replace=True,
    )

    assert user.full_name is None
    assert identity.external_display_name is None


@pytest.mark.asyncio
async def test_partial_update_still_preserves_omitted_display_name(monkeypatch):
    from app.services import scim_service

    user_id = uuid.uuid4()
    user = SimpleNamespace(
        id=user_id,
        username="alice",
        email="alice@example.test",
        full_name="Keep Me",
        is_active=True,
        role="viewer",
    )
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    db = AsyncMock()
    db.execute = AsyncMock(return_value=user_result)
    monkeypatch.setattr(scim_service, "log_identity_event", AsyncMock())

    await scim_service.scim_update_user(db, user_id, display_name=None)

    assert user.full_name == "Keep Me"


def test_replace_explicitly_selects_full_replace_semantics():
    from app.routers.scim import scim_replace

    assert "full_replace=True" in inspect.getsource(scim_replace)


@pytest.mark.parametrize(
    ("display_name", "name", "expected"),
    [
        ("Explicit", {"formatted": "Formatted", "givenName": "Given"}, "Explicit"),
        (None, {"formatted": "Formatted", "givenName": "Given"}, "Formatted"),
        (None, {"givenName": "Given", "familyName": "Family"}, "Given Family"),
        (None, None, None),
    ],
)
def test_request_display_name_preserves_formatted_name(display_name, name, expected):
    from app.models.schemas import SCIMUserRequest
    from app.routers.scim import _scim_display_name

    payload = SCIMUserRequest(
        schemas=[USER_SCHEMA],
        userName="alice",
        emails=[{"value": "alice@example.test"}],
        displayName=display_name,
        name=name,
    )
    assert _scim_display_name(payload) == expected


def test_create_and_replace_share_name_resolution():
    from app.routers.scim import scim_create, scim_replace

    assert "_scim_display_name(payload)" in inspect.getsource(scim_create)
    assert "_scim_display_name(payload)" in inspect.getsource(scim_replace)
