from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_scim_token_audit_event_values_fit_identity_column():
    from app.models.postgres import IdentityEvent, IdentityEventType

    width = IdentityEvent.__table__.c.event_type.type.length
    assert width is not None
    assert len(IdentityEventType.SCIM_TOKEN_CREATED.value) <= width
    assert len(IdentityEventType.SCIM_TOKEN_REVOKED.value) <= width


def _token():
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Okta provisioning",
        token_hint="scim_abc...",
        token_hash="super-secret-digest",
        sso_config_id=uuid.uuid4(),
        is_active=True,
        last_used_at=None,
        expires_at=datetime(2030, 1, 2, tzinfo=timezone.utc),
        created_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )


def _assert_audit_is_credential_safe(audit_call, raw_token: str) -> None:
    serialized = repr(audit_call.kwargs)
    assert raw_token not in serialized
    assert "super-secret-digest" not in serialized
    assert "token_hash" not in audit_call.kwargs["detail"]


@pytest.mark.asyncio
async def test_scim_token_creation_writes_safe_actor_audit(monkeypatch):
    from app.models.postgres import IdentityEventType
    from app.models.schemas import SCIMTokenCreate
    from app.routers import scim

    token = _token()
    raw_token = "scim_raw-secret-only-returned-once"
    create = AsyncMock(return_value=(token, raw_token))
    audit = AsyncMock()
    monkeypatch.setattr(scim, "create_scim_token", create)
    monkeypatch.setattr(scim, "log_identity_event", audit)
    db = MagicMock(commit=AsyncMock(), refresh=AsyncMock())
    actor = SimpleNamespace(id=uuid.uuid4(), username="admin")

    response = await scim.create_token(
        SCIMTokenCreate(name=token.name, sso_config_id=token.sso_config_id),
        request=SimpleNamespace(client=SimpleNamespace(host="192.0.2.10")),
        current_user=actor,
        db=db,
    )

    assert response.raw_token == raw_token
    assert audit.await_args.args[:2] == (db, IdentityEventType.SCIM_TOKEN_CREATED)
    assert audit.await_args.kwargs["actor_id"] == actor.id
    assert audit.await_args.kwargs["ip_address"] == "192.0.2.10"
    assert audit.await_args.kwargs["detail"] == {
        "token_id": str(token.id),
        "name": token.name,
        "token_hint": token.token_hint,
        "expires_at": token.expires_at.isoformat(),
    }
    _assert_audit_is_credential_safe(audit.await_args, raw_token)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_scim_token_revocation_writes_safe_actor_audit(monkeypatch):
    from app.models.postgres import IdentityEventType
    from app.routers import scim

    token = _token()
    selected = MagicMock()
    selected.scalar_one_or_none.return_value = token
    db = MagicMock(execute=AsyncMock(return_value=selected), commit=AsyncMock())
    audit = AsyncMock()
    monkeypatch.setattr(scim, "log_identity_event", audit)
    actor = SimpleNamespace(id=uuid.uuid4(), username="admin")

    await scim.revoke_scim_token(
        token.id,
        request=SimpleNamespace(client=SimpleNamespace(host="192.0.2.11")),
        current_user=actor,
        db=db,
    )

    assert token.is_active is False
    assert audit.await_args.args[:2] == (db, IdentityEventType.SCIM_TOKEN_REVOKED)
    assert audit.await_args.kwargs["actor_name"] == "admin"
    assert audit.await_args.kwargs["sso_config_id"] == token.sso_config_id
    assert audit.await_args.kwargs["detail"] == {
        "token_id": str(token.id),
        "name": token.name,
        "token_hint": token.token_hint,
    }
    _assert_audit_is_credential_safe(audit.await_args, "unused-raw-token")
    db.commit.assert_awaited_once()
