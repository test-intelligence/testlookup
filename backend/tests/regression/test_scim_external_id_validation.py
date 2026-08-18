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
        email="alice@example.test",
        full_name="Alice",
        is_active=True,
        role="VIEWER",
    )


@pytest.mark.asyncio
async def test_create_rejects_directory_external_id_collision_before_mutation():
    from app.services import scim_service

    config_id = uuid.uuid4()
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(SimpleNamespace(external_id="subject-1")))

    with pytest.raises(ValueError, match="already exists in this directory"):
        await scim_service.scim_create_user(
            db,
            username="alice",
            email="alice@example.test",
            external_id=" subject-1 ",
            sso_config_id=config_id,
        )

    statement = str(db.execute.await_args.args[0]).lower()
    assert "federated_identities.sso_config_id" in statement
    assert "federated_identities.external_id" in statement
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_create_stores_trimmed_case_exact_external_id(monkeypatch):
    from app.services import scim_service

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(None), _result(None), _result(None), _result(None)])
    db.flush = AsyncMock()
    monkeypatch.setattr(scim_service, "get_password_hash", lambda value: f"hash:{value}")
    monkeypatch.setattr(scim_service, "log_identity_event", AsyncMock())

    await scim_service.scim_create_user(
        db,
        username="alice",
        email="alice@example.test",
        external_id=" Subject-A ",
        sso_config_id=uuid.uuid4(),
    )

    identities = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], scim_service.FederatedIdentity)
    ]
    assert len(identities) == 1
    assert identities[0].external_id == "Subject-A"


@pytest.mark.asyncio
async def test_create_rejects_whitespace_external_id_before_database_access():
    from app.services import scim_service

    db = MagicMock()
    db.execute = AsyncMock()

    with pytest.raises(ValueError, match="non-empty"):
        await scim_service.scim_create_user(
            db,
            username="alice",
            email="alice@example.test",
            external_id="   ",
            sso_config_id=uuid.uuid4(),
        )

    db.execute.assert_not_awaited()
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_update_rejects_external_id_collision_before_user_mutation(monkeypatch):
    from app.services import scim_service

    user = _user()
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(user), _result(SimpleNamespace())])
    monkeypatch.setattr(scim_service, "log_identity_event", AsyncMock())

    with pytest.raises(ValueError, match="already exists in this directory"):
        await scim_service.scim_update_user(
            db,
            user.id,
            username="changed",
            external_id="subject-2",
            sso_config_id=uuid.uuid4(),
        )

    assert user.username == "alice"
    scim_service.log_identity_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_stores_trimmed_external_id(monkeypatch):
    from app.services import scim_service

    user = _user()
    identity = SimpleNamespace(
        external_id="old-subject",
        external_groups=[],
        external_email=user.email,
        external_display_name=user.full_name,
    )
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(user), _result(None), _result(identity)])
    monkeypatch.setattr(scim_service, "log_identity_event", AsyncMock())

    await scim_service.scim_update_user(
        db,
        user.id,
        external_id=" Subject-A ",
        sso_config_id=uuid.uuid4(),
    )

    assert identity.external_id == "Subject-A"
    assert scim_service.log_identity_event.await_args.kwargs["detail"]["external_id"] == {
        "old": "old-subject",
        "new": "Subject-A",
    }


@pytest.mark.asyncio
async def test_external_id_filter_trims_without_case_folding():
    from app.services.scim_service import scim_list_users

    identity_result = MagicMock()
    identity_result.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(return_value=identity_result)

    users, total = await scim_list_users(
        db,
        filter_str='externalId eq " Subject-A "',
        sso_config_id=uuid.uuid4(),
    )

    assert users == []
    assert total == 0
    statement = str(db.execute.await_args.args[0]).lower()
    assert "federated_identities.external_id" in statement
    assert "lower(" not in statement


@pytest.mark.asyncio
async def test_whitespace_external_id_filter_returns_empty_without_query():
    from app.services.scim_service import scim_list_users

    db = MagicMock()
    db.execute = AsyncMock()

    assert await scim_list_users(db, filter_str='externalId eq "   "') == ([], 0)
    db.execute.assert_not_awaited()
