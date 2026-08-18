from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _user():
    return SimpleNamespace(
        id=uuid.uuid4(),
        username="alice",
        email="alice@example.com",
        full_name="Alice",
        is_active=True,
        role="VIEWER",
        created_at=None,
        updated_at=None,
    )


@pytest.mark.asyncio
async def test_group_deltas_apply_in_order_and_recalculate_role(monkeypatch):
    from app.services import scim_service

    config_id = uuid.uuid4()
    user = _user()
    fed = SimpleNamespace(external_groups=["Viewer", "QA"])
    config = SimpleNamespace(
        role_mapping={"Admins": "ADMIN"},
        default_role="VIEWER",
    )
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(user), _result(fed), _result(config)])
    monkeypatch.setattr(scim_service, "log_identity_event", AsyncMock())
    monkeypatch.setattr(scim_service, "resolve_role_from_groups", lambda groups, *_: groups[-1])

    await scim_service.scim_update_user(
        db,
        user.id,
        sso_config_id=config_id,
        group_operations=[
            ("add", ["Admins", "QA"]),
            ("remove", ["Viewer"]),
            ("add", ["Release"]),
        ],
    )

    assert fed.external_groups == [
        {"value": "QA"},
        {"value": "Admins"},
        {"value": "Release"},
    ]
    assert user.role == "Release"
    assert db.execute.await_count == 3
    detail = scim_service.log_identity_event.await_args.kwargs["detail"]
    assert detail["groups"] == {
        "old": [{"value": "QA"}, {"value": "Viewer"}],
        "new": [
            {"value": "Admins"},
            {"value": "QA"},
            {"value": "Release"},
        ],
    }
    assert detail["role"] == {"old": "VIEWER", "new": "Release"}


@pytest.mark.asyncio
async def test_group_delta_requires_bound_identity_before_changes(monkeypatch):
    from app.services import scim_service

    user = _user()
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(user))
    audit = AsyncMock()
    monkeypatch.setattr(scim_service, "log_identity_event", audit)

    with pytest.raises(ValueError, match="IdP-bound"):
        await scim_service.scim_update_user(
            db,
            user.id,
            group_operations=[("add", ["Admins"])],
        )

    assert user.role == "VIEWER"
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_preserves_ordered_add_remove_and_remove_all(monkeypatch):
    from app.models.schemas import SCIMPatchOp, SCIMPatchRequest
    from app.routers import scim as router

    user = _user()
    update = AsyncMock(return_value=user)
    monkeypatch.setattr(router, "scim_update_user", update)
    monkeypatch.setattr(router, "scim_identity_map", AsyncMock(return_value={}))
    payload = SCIMPatchRequest(
        Operations=[
            SCIMPatchOp(op="add", path="groups", value=[{"display": "Admins"}]),
            SCIMPatchOp(op="remove", path='groups[value eq "QA"]'),
            SCIMPatchOp(op="remove", path="groups"),
        ]
    )
    request = SimpleNamespace(client=None, base_url="https://example.test/")
    token = SimpleNamespace(sso_config_id=uuid.uuid4())

    await router.scim_patch(uuid.uuid4(), payload, request, token, AsyncMock())

    assert update.await_args.kwargs["group_operations"] == [
        ("add", [{"value": "Admins", "display": "Admins"}]),
        ("remove", [{"value": "QA"}]),
        ("replace", []),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("removed", ["group-admin-id", "Administrators"])
async def test_filtered_removal_matches_stable_value_or_display(monkeypatch, removed):
    from app.services import scim_service

    config_id = uuid.uuid4()
    user = _user()
    fed = SimpleNamespace(
        external_groups=[
            {"value": "group-admin-id", "display": "Administrators"},
            {"value": "group-qa-id", "display": "QA"},
        ]
    )
    config = SimpleNamespace(role_mapping={}, default_role="VIEWER")
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(user), _result(fed), _result(config)])
    monkeypatch.setattr(scim_service, "log_identity_event", AsyncMock())

    await scim_service.scim_update_user(
        db,
        user.id,
        sso_config_id=config_id,
        group_operations=[("remove", [{"value": removed}])],
    )

    assert fed.external_groups == [{"value": "group-qa-id", "display": "QA"}]
    assert "no_changes" not in scim_service.log_identity_event.await_args.kwargs["detail"]


@pytest.mark.asyncio
async def test_group_reordering_is_not_audited_as_a_semantic_change(monkeypatch):
    from app.services import scim_service

    config_id = uuid.uuid4()
    user = _user()
    fed = SimpleNamespace(external_groups=[{"value": "b"}, {"value": "a"}])
    config = SimpleNamespace(role_mapping={}, default_role="VIEWER")
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(user), _result(config), _result(fed)])
    monkeypatch.setattr(scim_service, "log_identity_event", AsyncMock())

    await scim_service.scim_update_user(
        db,
        user.id,
        sso_config_id=config_id,
        groups=["a", "b"],
        group_refs=[{"value": "a"}, {"value": "b"}],
    )

    assert scim_service.log_identity_event.await_args.kwargs["detail"] == {
        "no_changes": True
    }


@pytest.mark.asyncio
async def test_create_preserves_group_value_and_display(monkeypatch):
    from app.models.schemas import SCIMUserResource
    from app.routers import scim as router

    user = _user()
    create = AsyncMock(return_value=user)
    monkeypatch.setattr(router, "scim_create_user", create)
    monkeypatch.setattr(router, "scim_identity_map", AsyncMock(return_value={}))
    payload = SCIMUserResource(
        userName="alice",
        externalId="external-alice",
        emails=[{"value": "alice@example.com", "primary": True}],
        groups=[{"value": "group-admin-id", "display": "Administrators"}],
    )
    request = SimpleNamespace(client=None, base_url="https://example.test/")
    token = SimpleNamespace(sso_config_id=uuid.uuid4())

    await router.scim_create(payload, request, token, AsyncMock())

    assert create.await_args.kwargs["groups"] == ["Administrators"]
    assert create.await_args.kwargs["group_refs"] == [
        {"value": "group-admin-id", "display": "Administrators"}
    ]
