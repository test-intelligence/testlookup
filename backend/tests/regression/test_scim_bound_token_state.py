from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _token(*, sso_config_id=...):
    return SimpleNamespace(
        sso_config_id=(uuid.uuid4() if sso_config_id is ... else sso_config_id),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        last_used_at=None,
    )


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("config_state", [False, None])
async def test_bound_scim_token_requires_existing_active_sso_config(config_state):
    from app.services.scim_service import validate_scim_token

    token = _token()
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(token), _result(config_state)])

    assert await validate_scim_token(db, "scim_secret") is None
    assert db.execute.await_count == 2
    assert token.last_used_at is None


@pytest.mark.asyncio
async def test_bound_scim_token_is_valid_while_sso_config_is_active():
    from app.services.scim_service import validate_scim_token

    token = _token()
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(token), _result(True)])

    assert await validate_scim_token(db, "scim_secret") is token
    assert token.last_used_at is not None
    binding_query = db.execute.await_args_list[1].args[0].compile()
    assert token.sso_config_id in binding_query.params.values()
    assert "sso_configurations.is_active" in str(binding_query)


@pytest.mark.asyncio
async def test_unbound_scim_token_remains_system_wide_without_config_lookup():
    from app.services.scim_service import validate_scim_token

    token = _token(sso_config_id=None)
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(token))

    assert await validate_scim_token(db, "scim_secret") is token
    db.execute.assert_awaited_once()
    assert token.last_used_at is not None


@pytest.mark.asyncio
async def test_expired_bound_token_fails_before_config_lookup():
    from app.services.scim_service import validate_scim_token

    token = _token()
    token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(token))

    assert await validate_scim_token(db, "scim_secret") is None
    db.execute.assert_awaited_once()
    assert token.last_used_at is None
