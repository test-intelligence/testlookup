import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"


def _user_payload(**overrides):
    values = {
        "schemas": [USER_SCHEMA],
        "userName": "alice",
        "emails": [{"value": "alice@example.com"}],
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("field", "accepted", "rejected"),
    [
        ("userName", "u" * 100, "u" * 101),
        ("externalId", "e" * 1000, "e" * 1001),
        ("displayName", "d" * 255, "d" * 256),
    ],
)
def test_user_scalar_limits_match_persistence_columns(field, accepted, rejected):
    from app.models.schemas import SCIMUserRequest

    assert getattr(SCIMUserRequest(**_user_payload(**{field: accepted})), field) == accepted
    with pytest.raises(ValidationError):
        SCIMUserRequest(**_user_payload(**{field: rejected}))


def test_derived_display_name_must_fit_local_user_column():
    from app.models.schemas import SCIMUserRequest

    accepted = SCIMUserRequest(
        **_user_payload(name={"givenName": "g" * 127, "familyName": "f" * 127})
    )
    assert accepted.name is not None
    with pytest.raises(ValidationError, match="derived displayName must be at most 255"):
        SCIMUserRequest(
            **_user_payload(name={"givenName": "g" * 128, "familyName": "f" * 127})
        )


def test_email_length_is_validated_before_database_work():
    from app.models.schemas import SCIMUserRequest

    assert SCIMUserRequest(
        **_user_payload(emails=[{"value": "alice@example.test"}])
    ).emails[0].value == "alice@example.test"
    with pytest.raises(ValidationError):
        SCIMUserRequest(**_user_payload(emails=[{"value": "e" * 256}]))


@pytest.mark.parametrize(
    ("field", "limit", "item"),
    [
        ("emails", 100, {"value": "alice@example.com"}),
        ("groups", 1000, {"value": "engineering"}),
    ],
)
def test_user_collection_limits_have_accepted_and_rejected_boundaries(field, limit, item):
    from app.models.schemas import SCIMUserRequest

    accepted = SCIMUserRequest(**_user_payload(**{field: [item] * limit}))
    assert len(getattr(accepted, field)) == limit
    with pytest.raises(ValidationError):
        SCIMUserRequest(**_user_payload(**{field: [item] * (limit + 1)}))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "detail"),
    [
        (
            {"op": "replace", "path": "userName", "value": "u" * 101},
            "userName must be at most 100 characters",
        ),
        (
            {"op": "replace", "path": "externalId", "value": "e" * 1001},
            "externalId must be at most 1000 characters",
        ),
        (
            {"op": "replace", "path": "displayName", "value": "d" * 256},
            "displayName must be at most 255 characters",
        ),
        (
            {"op": "replace", "path": "emails", "value": [{"value": "e" * 256}]},
            "emails must contain a valid value",
        ),
        (
            {"op": "replace", "path": "groups", "value": [{"value": "g"}] * 1001},
            "groups must be an array",
        ),
    ],
)
async def test_patch_limits_reject_before_mutation(monkeypatch, operation, detail):
    from app.models.schemas import SCIMPatchRequestPayload
    from app.routers import scim

    update = AsyncMock()
    monkeypatch.setattr(scim, "scim_update_user", update)
    payload = SCIMPatchRequestPayload(schemas=[PATCH_SCHEMA], Operations=[operation])

    with pytest.raises(HTTPException) as raised:
        await scim.scim_patch(
            uuid.uuid4(),
            payload,
            MagicMock(client=None),
            SimpleNamespace(sso_config_id=uuid.uuid4()),
            AsyncMock(),
        )

    assert raised.value.status_code == 400
    assert raised.value.detail == detail
    update.assert_not_awaited()
