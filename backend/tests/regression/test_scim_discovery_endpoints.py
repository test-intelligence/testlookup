import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("idp.example.test", 443),
            "root_path": "",
            "path": path,
            "query_string": b"",
            "headers": [],
        }
    )


def _token():
    return SimpleNamespace(id=uuid.uuid4(), sso_config_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_service_provider_config_matches_implemented_capabilities():
    from app.routers.scim import scim_service_provider_config

    document = await scim_service_provider_config(
        _request("/api/v1/scim/v2/ServiceProviderConfig"),
        _token(),
    )

    assert document["schemas"] == [
        "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
    ]
    assert document["patch"] == {"supported": True}
    assert document["filter"] == {"supported": True, "maxResults": 200}
    assert document["bulk"]["supported"] is False
    assert document["changePassword"]["supported"] is False
    assert document["sort"]["supported"] is False
    assert document["etag"]["supported"] is False
    assert document["authenticationSchemes"][0]["primary"] is True
    assert document["meta"]["location"] == (
        "https://idp.example.test/api/v1/scim/v2/ServiceProviderConfig"
    )


@pytest.mark.asyncio
async def test_resource_type_list_and_item_share_one_user_contract():
    from app.routers.scim import scim_resource_type, scim_resource_types

    request = _request("/api/v1/scim/v2/ResourceTypes")
    listing = await scim_resource_types(request, _token())
    item = await scim_resource_type("user", request, _token())

    assert listing["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:ListResponse"]
    assert listing["totalResults"] == 1
    assert listing["itemsPerPage"] == 1
    assert listing["Resources"] == [item]
    assert item["id"] == "User"
    assert item["endpoint"] == "/Users"
    assert item["schema"] == "urn:ietf:params:scim:schemas:core:2.0:User"
    assert "schemaExtensions" not in item


@pytest.mark.asyncio
async def test_schema_list_describes_only_attributes_the_user_api_exposes():
    from app.routers.scim import scim_schema, scim_schemas

    request = _request("/api/v1/scim/v2/Schemas")
    listing = await scim_schemas(request, _token())
    item = await scim_schema(
        "urn:ietf:params:scim:schemas:core:2.0:User",
        request,
        _token(),
    )

    assert listing["totalResults"] == 1
    assert listing["Resources"] == [item]
    assert item["schemas"] == ["urn:ietf:params:scim:schemas:core:2.0:Schema"]
    assert item["id"] == "urn:ietf:params:scim:schemas:core:2.0:User"
    attributes = {attribute["name"]: attribute for attribute in item["attributes"]}
    assert set(attributes) == {
        "userName",
        "externalId",
        "name",
        "displayName",
        "active",
        "emails",
        "groups",
    }
    assert attributes["userName"]["required"] is True
    assert attributes["userName"]["caseExact"] is False
    assert attributes["userName"]["uniqueness"] == "server"
    assert attributes["externalId"]["caseExact"] is True
    assert attributes["emails"]["multiValued"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "unknown"),
    [("resource_type", "Group"), ("schema", "urn:example:unsupported")],
)
async def test_unknown_discovery_item_returns_scim_404(endpoint, unknown):
    from app.routers import scim

    handler = scim.scim_resource_type if endpoint == "resource_type" else scim.scim_schema
    with pytest.raises(HTTPException) as raised:
        await handler(
            unknown,
            _request(f"/api/v1/scim/v2/{unknown}"),
            _token(),
        )

    assert raised.value.status_code == 404


def test_discovery_documents_are_isolated_between_requests():
    from app.services.scim_discovery import user_schema

    first = user_schema("https://first.example/scim/v2")
    first["attributes"][0]["name"] = "corrupted"
    second = user_schema("https://second.example/scim/v2")

    assert second["attributes"][0]["name"] == "userName"
    assert second["meta"]["location"].startswith("https://second.example/")
