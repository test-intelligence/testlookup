import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (r'groups[value eq "Release\"Managers"]', 'Release"Managers'),
        (r'groups[value eq "caf\u00e9"]', "café"),
    ],
)
def test_group_filter_decodes_json_string_values(path, expected):
    from app.routers.scim import _scim_group_filter_name

    assert _scim_group_filter_name(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        'groups[value eq "Release"Managers"]',
        "groups[value eq 'Admins']",
        r'groups[value eq "bad\qescape"]',
        'groups[value eq ""]',
    ],
)
def test_group_filter_rejects_non_json_or_empty_strings(path):
    from app.routers.scim import _scim_group_filter_name

    assert _scim_group_filter_name(path) is None


@pytest.mark.asyncio
async def test_escaped_group_filter_reaches_service_with_decoded_value(monkeypatch):
    from app.models.schemas import SCIMPatchOp, SCIMPatchRequestPayload
    from app.routers import scim

    update = AsyncMock(side_effect=RuntimeError("stop after parsing"))
    monkeypatch.setattr(scim, "scim_update_user", update)
    payload = SCIMPatchRequestPayload(
        schemas=[PATCH_SCHEMA],
        Operations=[
            SCIMPatchOp(
                op="remove",
                path=r'groups[value eq "Release\"Managers"]',
            )
        ],
    )

    with pytest.raises(RuntimeError, match="stop after parsing"):
        await scim.scim_patch(
            uuid.uuid4(),
            payload,
            MagicMock(client=None),
            SimpleNamespace(sso_config_id=uuid.uuid4()),
            AsyncMock(),
        )

    assert update.await_args.kwargs["group_operations"] == [
        ("remove", [{"value": 'Release"Managers'}])
    ]


@pytest.mark.asyncio
async def test_malformed_group_filter_is_rejected_before_mutation(monkeypatch):
    from app.models.schemas import SCIMPatchOp, SCIMPatchRequestPayload
    from app.routers import scim

    update = AsyncMock()
    monkeypatch.setattr(scim, "scim_update_user", update)
    path = 'groups[value eq "Release"Managers"]'
    payload = SCIMPatchRequestPayload(
        schemas=[PATCH_SCHEMA],
        Operations=[SCIMPatchOp(op="remove", path=path)],
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
    assert raised.value.detail == f"Unsupported PATCH path: {path}"
    update.assert_not_awaited()
