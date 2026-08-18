import inspect

import pytest
from pydantic import ValidationError


USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
EXTENSION_SCHEMA = "urn:example:params:scim:schemas:extension:2.0:User"


def test_public_mutation_endpoints_use_strict_request_envelopes():
    from app.routers import scim
    from app.models.schemas import SCIMPatchRequestPayload, SCIMUserRequest

    assert inspect.signature(scim.scim_create).parameters["payload"].annotation is SCIMUserRequest
    assert inspect.signature(scim.scim_replace).parameters["payload"].annotation is SCIMUserRequest
    assert (
        inspect.signature(scim.scim_patch).parameters["payload"].annotation
        is SCIMPatchRequestPayload
    )


@pytest.mark.parametrize("schemas", [[], ["urn:example:wrong"]])
def test_user_payload_rejects_missing_core_schema_when_supplied(schemas):
    from app.models.schemas import SCIMUserRequest

    with pytest.raises(ValidationError, match="schemas must include"):
        SCIMUserRequest(schemas=schemas, userName="alice")


def test_user_payload_default_and_extensions_preserve_core_schema():
    from app.models.schemas import SCIMUserRequest, SCIMUserResource

    assert SCIMUserResource(userName="alice").schemas == [USER_SCHEMA]
    with pytest.raises(ValidationError, match="Field required"):
        SCIMUserRequest(userName="alice")
    assert SCIMUserRequest(
        schemas=[USER_SCHEMA, EXTENSION_SCHEMA],
        userName="alice",
    ).schemas == [USER_SCHEMA, EXTENSION_SCHEMA]


@pytest.mark.parametrize("schemas", [[], [USER_SCHEMA], ["urn:example:wrong"]])
def test_patch_payload_rejects_missing_patchop_schema_when_supplied(schemas):
    from app.models.schemas import SCIMPatchRequestPayload

    with pytest.raises(ValidationError, match="schemas must include"):
        SCIMPatchRequestPayload(schemas=schemas, Operations=[])


def test_patch_payload_default_and_extensions_preserve_patchop_schema():
    from app.models.schemas import SCIMPatchRequest, SCIMPatchRequestPayload

    assert SCIMPatchRequest(Operations=[]).schemas == [PATCH_SCHEMA]
    with pytest.raises(ValidationError, match="Field required"):
        SCIMPatchRequestPayload(Operations=[])
    assert SCIMPatchRequestPayload(
        schemas=[PATCH_SCHEMA, EXTENSION_SCHEMA],
        Operations=[],
    ).schemas == [PATCH_SCHEMA, EXTENSION_SCHEMA]
