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

    assert fed.external_groups == ["QA", "Admins", "Release"]
    assert user.role == "Release"
    assert db.execute.await_count == 3


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
        ("add", ["Admins"]),
        ("remove", ["QA"]),
        ("replace", []),
    ]
