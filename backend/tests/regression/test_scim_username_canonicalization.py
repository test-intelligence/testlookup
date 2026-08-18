import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _user(username="Alice"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        username=username,
        email="alice@example.test",
        full_name="Alice",
        is_active=True,
        role="VIEWER",
    )


@pytest.mark.asyncio
async def test_create_detects_case_insensitive_existing_username():
    from app.services import scim_service

    existing = _user()
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(None), _result(existing)])

    with pytest.raises(ValueError, match="already exists"):
        await scim_service.scim_create_user(
            db,
            username="alice",
            email="new@example.test",
        )

    statement = db.execute.await_args_list[1].args[0]
    assert "lower(users.username)" in str(statement).lower()
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_create_stores_canonical_username(monkeypatch):
    from app.services import scim_service

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(None), _result(None)])
    db.flush = AsyncMock()
    monkeypatch.setattr(scim_service, "get_password_hash", lambda value: f"hash:{value}")
    monkeypatch.setattr(scim_service, "log_identity_event", AsyncMock())

    user = await scim_service.scim_create_user(
        db,
        username="  Alice  ",
        email="alice@example.test",
    )

    assert user.username == "alice"
    assert db.add.call_args.args[0] is user


@pytest.mark.asyncio
async def test_create_rejects_whitespace_username_before_database_access():
    from app.services import scim_service

    db = MagicMock()
    db.execute = AsyncMock()

    with pytest.raises(ValueError, match="non-empty"):
        await scim_service.scim_create_user(
            db,
            username="   ",
            email="alice@example.test",
        )

    db.execute.assert_not_awaited()
    db.add.assert_not_called()


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
        username="alice",
    )

    assert updated.username == "alice"
    duplicate_statement = db.execute.await_args_list[1].args[0]
    assert "lower(users.username)" in str(duplicate_statement).lower()


@pytest.mark.asyncio
async def test_update_rejects_case_insensitive_collision_without_mutating(monkeypatch):
    from app.services import scim_service

    user = _user("owner")
    other = _user("Alice")
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(user), _result(other)])
    monkeypatch.setattr(scim_service, "log_identity_event", AsyncMock())

    with pytest.raises(ValueError, match="already taken"):
        await scim_service.scim_update_user(
            db,
            user.id,
            username="alice",
        )

    assert user.username == "owner"
    scim_service.log_identity_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_username_filter_uses_case_insensitive_identity_comparison():
    from app.services.scim_service import scim_list_users

    count_result = MagicMock()
    count_result.scalar.return_value = 0
    db = MagicMock()
    db.execute = AsyncMock(return_value=count_result)

    users, total = await scim_list_users(
        db,
        filter_str='userName eq " Alice "',
    )

    assert users == []
    assert total == 0
    count_statement = db.execute.await_args.args[0]
    statement = str(count_statement).lower()
    assert "lower(users.username)" in statement
