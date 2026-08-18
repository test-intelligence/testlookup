import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _resource():
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": "user-1",
        "externalId": "subject-1",
        "userName": "alice",
        "displayName": "Alice Example",
        "active": True,
        "name": {"givenName": "Alice", "familyName": "Example"},
        "emails": [{"value": "alice@example.test", "type": "work", "primary": True}],
        "groups": [{"value": "qa", "display": "QA"}],
        "meta": {"resourceType": "User", "location": "https://example.test/Users/user-1"},
    }


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("example.test", 443),
            "root_path": "",
            "path": "/api/v1/scim/v2/Users/user-1",
            "query_string": b"",
            "headers": [],
        }
    )


def _user():
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        username="alice",
        email="alice@example.test",
        full_name="Alice Example",
        is_active=True,
        role="VIEWER",
        created_at=now,
        updated_at=now,
    )


def test_included_attributes_preserve_always_returned_identity_and_nested_paths():
    from app.services.scim_projection import parse_scim_projection, project_scim_resource

    source = _resource()
    projection = parse_scim_projection(
        "userName,name.givenName,emails.value",
        None,
    )
    result = project_scim_resource(source, projection)

    assert result == {
        "schemas": source["schemas"],
        "id": "user-1",
        "userName": "alice",
        "name": {"givenName": "Alice"},
        "emails": [{"value": "alice@example.test"}],
    }
    assert source == _resource()


def test_excluded_attributes_remove_top_level_and_nested_values_only():
    from app.services.scim_projection import parse_scim_projection, project_scim_resource

    projection = parse_scim_projection(None, "displayName,emails.type,groups.display,id")
    result = project_scim_resource(_resource(), projection)

    assert "displayName" not in result
    assert result["emails"] == [{"value": "alice@example.test", "primary": True}]
    assert result["groups"] == [{"value": "qa"}]
    assert result["id"] == "user-1"


def test_projection_accepts_case_insensitive_and_schema_qualified_paths():
    from app.services.scim_projection import parse_scim_projection, project_scim_resource

    projection = parse_scim_projection(
        "USERNAME,urn:ietf:params:scim:schemas:core:2.0:User:NAME.FAMILYNAME",
        None,
    )
    result = project_scim_resource(_resource(), projection)

    assert result["userName"] == "alice"
    assert result["name"] == {"familyName": "Example"}


@pytest.mark.parametrize(
    ("attributes", "excluded"),
    [
        ("userName", "emails"),
        ("userName,,emails", None),
        ("name[familyName]", None),
        (",".join(f"attr{index}" for index in range(101)), None),
        ("x" * 2001, None),
    ],
)
def test_invalid_projection_is_rejected(attributes, excluded):
    from app.services.scim_projection import SCIMProjectionError, parse_scim_projection

    with pytest.raises(SCIMProjectionError):
        parse_scim_projection(attributes, excluded)


@pytest.mark.asyncio
async def test_get_applies_projection_after_identity_rendering(monkeypatch):
    from app.routers import scim

    user = _user()
    monkeypatch.setattr(scim, "scim_get_user", AsyncMock(return_value=user))
    monkeypatch.setattr(scim, "scim_identity_map", AsyncMock(return_value={}))

    result = await scim.scim_get(
        user.id,
        _request(),
        SimpleNamespace(sso_config_id=None),
        AsyncMock(),
        attributes="userName",
    )

    assert result == {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": str(user.id),
        "userName": "alice",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_name", ["scim_create", "scim_replace", "scim_patch"])
async def test_mutation_routes_reject_conflicting_projection_before_database_access(handler_name):
    from app.routers import scim

    db = AsyncMock()
    handler = getattr(scim, handler_name)
    common = {
        "request": _request(),
        "scim_token": SimpleNamespace(sso_config_id=None),
        "db": db,
        "attributes": "userName",
        "excludedAttributes": "emails",
    }

    with pytest.raises(HTTPException) as raised:
        if handler_name == "scim_create":
            await handler(payload=MagicMock(), **common)
        else:
            await handler(user_id=uuid.uuid4(), payload=MagicMock(), **common)

    assert raised.value.status_code == 400
    assert raised.value.detail["scimType"] == "invalidValue"
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()
