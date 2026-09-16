"""Password changes and session issuance must serialize on the user row.

Without this lock, a login can verify the old password while a reset is still
uncommitted, then mint a session after the reset cutoff was written.  The new
session survives the operation that was meant to revoke every prior session.
"""
from __future__ import annotations

import ast
import inspect
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _calls_method(function, method: str) -> bool:
    tree = ast.parse(inspect.getsource(function))
    return any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == method)
            or (isinstance(node.func, ast.Name) and node.func.id == method)
        )
        for node in ast.walk(tree)
    )


@pytest.mark.regression
def test_password_login_locks_user_before_verifying_and_issuing_session():
    from app.routers import auth

    assert _calls_method(auth.login, "with_for_update"), (
        "password login must lock the user row so a concurrent password reset "
        "commits before credentials are verified or waits until this session is issued"
    )


@pytest.mark.regression
def test_refresh_locks_user_before_rotating_session():
    from app.routers import auth

    assert _calls_method(auth.refresh_tokens, "with_for_update")


@pytest.mark.regression
def test_mfa_session_exchange_locks_user_and_rejects_predating_challenge():
    from app.routers import mfa

    assert _calls_method(mfa._user_from_mfa_token, "with_for_update")
    assert _calls_method(mfa._user_from_mfa_token, "is_token_before_cutoff")


@pytest.mark.regression
@pytest.mark.asyncio
async def test_mfa_challenge_issued_before_cutoff_is_rejected(monkeypatch):
    from app.routers import mfa

    user_id = uuid.uuid4()
    user = SimpleNamespace(id=user_id, is_active=True)

    class Result:
        def scalar_one_or_none(self):
            return user

    class Session:
        async def execute(self, statement):
            assert statement._for_update_arg is not None
            return Result()

    async def not_used(_jti):
        return False

    cutoff_calls = []

    async def predates_cutoff(uid, iat):
        cutoff_calls.append((uid, iat))
        return True

    monkeypatch.setattr(
        mfa,
        "decode_token",
        lambda *_args, **_kwargs: {
            "sub": str(user_id),
            "jti": "challenge-jti",
            "iat": 2_000_000_000.125,
            "exp": 2_000_000_300,
        },
    )
    monkeypatch.setattr(mfa, "is_jti_revoked", not_used)
    monkeypatch.setattr(mfa, "is_token_before_cutoff", predates_cutoff)

    with pytest.raises(HTTPException) as exc:
        await mfa._user_from_mfa_token(Session(), "challenge", "mfa_challenge")

    assert exc.value.status_code == 401
    assert cutoff_calls == [(user_id, 2_000_000_000.125)]
