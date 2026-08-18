import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


@pytest.mark.parametrize(
    "group",
    [
        {"value": "v" * 1001},
        {"value": "valid", "display": "d" * 501},
    ],
)
def test_group_item_lengths_are_bounded(group):
    from app.models.schemas import SCIMGroup

    with pytest.raises(ValidationError):
        SCIMGroup(**group)


def test_exact_duplicates_collapse_and_fill_missing_display():
    from app.routers.scim import _scim_group_refs

    assert _scim_group_refs(
        [
            {"value": "group-1"},
            {"value": "group-1", "display": "Engineering"},
            {"value": "group-1", "display": "Engineering"},
        ]
    ) == [{"value": "group-1", "display": "Engineering"}]


def test_conflicting_displays_for_one_stable_id_are_rejected():
    from app.routers.scim import _scim_group_refs

    assert _scim_group_refs(
        [
            {"value": "group-1", "display": "Engineering"},
            {"value": "group-1", "display": "Finance"},
        ]
    ) is None


def test_legacy_stored_duplicates_normalize_deterministically():
    from app.services.scim_service import _normalize_group_refs

    assert _normalize_group_refs(
        [
            {"value": "group-1"},
            {"value": "group-1", "display": "Engineering"},
            {"value": "group-1", "display": "Later Value"},
            "group-2",
            "group-2",
        ]
    ) == [
        {"value": "group-1", "display": "Engineering"},
        {"value": "group-2"},
    ]


@pytest.mark.asyncio
async def test_create_rejects_conflicting_groups_before_mutation(monkeypatch):
    from app.models.schemas import SCIMUserResource
    from app.routers import scim

    create = AsyncMock()
    monkeypatch.setattr(scim, "scim_create_user", create)
    payload = SCIMUserResource(
        userName="alice",
        emails=[{"value": "alice@example.test"}],
        groups=[
            {"value": "group-1", "display": "Engineering"},
            {"value": "group-1", "display": "Finance"},
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
    assert raised.value.detail == "groups contain conflicting or invalid values"
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_collapses_exact_duplicate_groups_before_mutation(monkeypatch):
    from app.models.schemas import SCIMPatchRequestPayload
    from app.routers import scim

    update = AsyncMock(side_effect=RuntimeError("stop after parsing"))
    monkeypatch.setattr(scim, "scim_update_user", update)
    payload = SCIMPatchRequestPayload(
        schemas=[PATCH_SCHEMA],
        Operations=[
            {
                "op": "replace",
                "path": "groups",
                "value": [
                    {"value": "group-1", "display": "Engineering"},
                    {"value": "group-1", "display": "Engineering"},
                ],
            }
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
        ("replace", [{"value": "group-1", "display": "Engineering"}])
    ]


@pytest.mark.asyncio
async def test_patch_rejects_conflicting_duplicate_groups_before_mutation(monkeypatch):
    from app.models.schemas import SCIMPatchRequestPayload
    from app.routers import scim

    update = AsyncMock()
    monkeypatch.setattr(scim, "scim_update_user", update)
    payload = SCIMPatchRequestPayload(
        schemas=[PATCH_SCHEMA],
        Operations=[
            {
                "op": "replace",
                "path": "groups",
                "value": [
                    {"value": "group-1", "display": "Engineering"},
                    {"value": "group-1", "display": "Finance"},
                ],
            }
        ],
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
    update.assert_not_awaited()
