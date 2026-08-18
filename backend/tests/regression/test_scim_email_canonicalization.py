import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _user(email="Alice@Example.Test"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        username="alice",
        email=email,
        full_name="Alice",
        is_active=True,
        role="VIEWER",
    )


def test_selected_email_is_trimmed_and_casefolded():
    from app.routers.scim import _scim_patch_email

    assert _scim_patch_email(
        [{"value": "  Alice@Example.Test  ", "primary": True}]
    ) == "alice@example.test"


@pytest.mark.asyncio
async def test_create_detects_case_insensitive_existing_email(monkeypatch):
    from app.services import scim_service

    existing = _user()
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(existing))

    with pytest.raises(ValueError, match="already exists"):
        await scim_service.scim_create_user(
            db,
            username="new-user",
            email="alice@example.test",
        )

    statement = db.execute.await_args.args[0]
    assert "lower(users.email)" in str(statement).lower()
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_create_stores_canonical_email(monkeypatch):
    from app.services import scim_service

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(None), _result(None)])
    db.flush = AsyncMock()
    monkeypatch.setattr(scim_service, "get_password_hash", lambda value: f"hash:{value}")
    monkeypatch.setattr(scim_service, "log_identity_event", AsyncMock())

    user = await scim_service.scim_create_user(
        db,
        username="alice",
        email="  Alice@Example.Test ",
    )

    assert user.email == "alice@example.test"
    assert db.add.call_args.args[0] is user


@pytest.mark.asyncio
async def test_update_canonicalizes_case_only_change(monkeypatch):
    from app.services import scim_service

    user = _user()
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(user), _result(None)])
    monkeypatch.setattr(scim_service, "log_identity_event", AsyncMock())

    updated = await scim_service.scim_update_user(
        db,
        user.id,
        email="alice@example.test",
    )

    assert updated.email == "alice@example.test"
    duplicate_statement = db.execute.await_args_list[1].args[0]
    assert "lower(users.email)" in str(duplicate_statement).lower()


@pytest.mark.asyncio
async def test_update_rejects_case_insensitive_collision_without_mutating(monkeypatch):
    from app.services import scim_service

    user = _user("owner@example.test")
    other = _user("Alice@Example.Test")
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(user), _result(other)])
    monkeypatch.setattr(scim_service, "log_identity_event", AsyncMock())

    with pytest.raises(ValueError, match="already taken"):
        await scim_service.scim_update_user(
            db,
            user.id,
            email="alice@example.test",
        )

    assert user.email == "owner@example.test"
    scim_service.log_identity_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_bound_email_only_update_syncs_federated_identity(monkeypatch):
    from app.services import scim_service

    user = _user("old@example.test")
    config_id = uuid.uuid4()
    identity = SimpleNamespace(external_email="old@example.test")
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[_result(user), _result(None), _result(identity)]
    )
    monkeypatch.setattr(scim_service, "log_identity_event", AsyncMock())

    await scim_service.scim_update_user(
        db,
        user.id,
        email=" New@Example.Test ",
        sso_config_id=config_id,
    )

    assert user.email == "new@example.test"
    assert identity.external_email == "new@example.test"
    assert db.execute.await_count == 3
    assert scim_service.log_identity_event.await_args.kwargs["detail"] == {
        "email": {"old": "old@example.test", "new": "new@example.test"},
        "directory_email": {
            "old": "old@example.test",
            "new": "new@example.test",
        },
    }


@pytest.mark.asyncio
async def test_stale_directory_email_sync_is_audited_when_local_email_matches(
    monkeypatch,
):
    from app.services import scim_service

    user = _user("current@example.test")
    config_id = uuid.uuid4()
    identity = SimpleNamespace(external_email="stale@example.test")
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(user), _result(identity)])
    monkeypatch.setattr(scim_service, "log_identity_event", AsyncMock())

    await scim_service.scim_update_user(
        db,
        user.id,
        email=" Current@Example.Test ",
        sso_config_id=config_id,
    )

    assert user.email == "current@example.test"
    assert identity.external_email == "current@example.test"
    assert scim_service.log_identity_event.await_args.kwargs["detail"] == {
        "directory_email": {
            "old": "stale@example.test",
            "new": "current@example.test",
        }
    }


@pytest.mark.asyncio
async def test_email_filter_uses_case_insensitive_identity_comparison():
    from app.services.scim_service import scim_list_users

    count_result = MagicMock()
    count_result.scalar.return_value = 0
    db = MagicMock()
    db.execute = AsyncMock(return_value=count_result)

    users, total = await scim_list_users(
        db,
        filter_str='emails.value eq "Alice@Example.Test"',
    )

    assert users == []
    assert total == 0
    count_statement = db.execute.await_args.args[0]
    assert "lower(users.email)" in str(count_statement).lower()
