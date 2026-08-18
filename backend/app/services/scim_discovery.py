"""Static SCIM discovery documents describing TestLookup's implemented surface."""

from copy import deepcopy

LIST_RESPONSE_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SERVICE_PROVIDER_CONFIG_SCHEMA = (
    "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
)
RESOURCE_TYPE_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
SCHEMA_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Schema"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"


def _string_attribute(
    name: str,
    *,
    required: bool = False,
    case_exact: bool = False,
    uniqueness: str = "none",
    mutability: str = "readWrite",
) -> dict:
    return {
        "name": name,
        "type": "string",
        "multiValued": False,
        "required": required,
        "caseExact": case_exact,
        "mutability": mutability,
        "returned": "default",
        "uniqueness": uniqueness,
    }


_NAME_SUB_ATTRIBUTES = [
    _string_attribute("givenName"),
    _string_attribute("familyName"),
]

_EMAIL_SUB_ATTRIBUTES = [
    _string_attribute("value"),
    {
        **_string_attribute("type"),
        "canonicalValues": ["work", "home", "other"],
    },
    {
        "name": "primary",
        "type": "boolean",
        "multiValued": False,
        "required": False,
        "mutability": "readWrite",
        "returned": "default",
    },
]

_GROUP_SUB_ATTRIBUTES = [
    _string_attribute("value"),
    _string_attribute("display"),
]

_USER_SCHEMA_DOCUMENT = {
    "schemas": [SCHEMA_SCHEMA],
    "id": USER_SCHEMA,
    "name": "User",
    "description": "TestLookup user account",
    "attributes": [
        _string_attribute("userName", required=True, uniqueness="server"),
        _string_attribute("externalId", case_exact=True),
        {
            "name": "name",
            "type": "complex",
            "multiValued": False,
            "required": False,
            "mutability": "readWrite",
            "returned": "default",
            "subAttributes": _NAME_SUB_ATTRIBUTES,
        },
        _string_attribute("displayName"),
        {
            "name": "active",
            "type": "boolean",
            "multiValued": False,
            "required": False,
            "mutability": "readWrite",
            "returned": "default",
        },
        {
            "name": "emails",
            "type": "complex",
            "multiValued": True,
            "required": True,
            "mutability": "readWrite",
            "returned": "default",
            "subAttributes": _EMAIL_SUB_ATTRIBUTES,
        },
        {
            "name": "groups",
            "type": "complex",
            "multiValued": True,
            "required": False,
            "mutability": "readWrite",
            "returned": "default",
            "subAttributes": _GROUP_SUB_ATTRIBUTES,
        },
    ],
}


def _location(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"


def service_provider_configuration(base_url: str) -> dict:
    """Describe only capabilities implemented by this SCIM service."""
    return {
        "schemas": [SERVICE_PROVIDER_CONFIG_SCHEMA],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "Bearer Token",
                "description": "SCIM bearer token supplied in the Authorization header",
                "specUri": "https://www.rfc-editor.org/rfc/rfc6750",
                "primary": True,
            }
        ],
        "meta": {
            "resourceType": "ServiceProviderConfig",
            "location": _location(base_url, "ServiceProviderConfig"),
        },
    }


def user_resource_type(base_url: str) -> dict:
    """Return the sole provisionable resource type."""
    return {
        "schemas": [RESOURCE_TYPE_SCHEMA],
        "id": "User",
        "name": "User",
        "endpoint": "/Users",
        "description": "TestLookup user account",
        "schema": USER_SCHEMA,
        "meta": {
            "resourceType": "ResourceType",
            "location": _location(base_url, "ResourceTypes/User"),
        },
    }


def user_schema(base_url: str) -> dict:
    """Return an isolated copy of the implemented User schema document."""
    document = deepcopy(_USER_SCHEMA_DOCUMENT)
    document["meta"] = {
        "resourceType": "Schema",
        "location": _location(base_url, f"Schemas/{USER_SCHEMA}"),
    }
    return document


def discovery_list(resources: list[dict]) -> dict:
    """Wrap discovery resources in the SCIM ListResponse contract."""
    return {
        "schemas": [LIST_RESPONSE_SCHEMA],
        "totalResults": len(resources),
        "startIndex": 1,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }
