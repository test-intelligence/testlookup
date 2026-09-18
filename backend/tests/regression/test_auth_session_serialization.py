"""Password changes and session issuance must serialize on the user row.

Without this lock, a login can verify the old password while a reset is still
uncommitted, then mint a session after the reset cutoff was written.  The new
session survives the operation that was meant to revoke every prior session.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
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
def test_every_direct_session_issuer_locks_the_user_row():
    from app.routers import auth, sso

    assert _calls_method(auth.dev_login, "with_for_update")
    assert _calls_method(sso.saml_acs, "with_for_update")


@pytest.mark.regression
def test_every_password_change_locks_the_user_row():
    from app.routers import auth
    from app.services import default_qa_lead_service

    assert _calls_method(auth.first_time_reset, "with_for_update")
    assert _calls_method(auth.change_password, "with_for_update")
    assert _calls_method(default_qa_lead_service.reset_default_qa_lead_password, "with_for_update")
    assert "revoke_all_user_tokens(user.id, db)" in inspect.getsource(
        default_qa_lead_service.reset_default_qa_lead_password
    )


@pytest.mark.regression
def test_router_session_tokens_use_the_database_clock_wrapper():
    from app.routers import auth, mfa, sso

    for module in (auth, mfa, sso):
        source = pathlib.Path(inspect.getsourcefile(module) or "").read_text(encoding="utf-8")
        assert "create_access_token(" not in source
        assert "create_mfa_token(" not in source
    assert "issue_access_jwt(" in inspect.getsource(auth)
    assert "issue_mfa_jwt(" in inspect.getsource(auth)
    assert "issue_access_jwt(" in inspect.getsource(mfa)
    assert "issue_access_jwt(" in inspect.getsource(sso)


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
            "iat": 2_000_000_000.100,
            "exp": 2_000_000_300,
        },
    )
    monkeypatch.setattr(mfa, "is_jti_revoked", not_used)
    monkeypatch.setattr(mfa, "is_token_before_cutoff", predates_cutoff)

    with pytest.raises(HTTPException) as exc:
        await mfa._user_from_mfa_token(Session(), "challenge", "mfa_challenge")

    assert exc.value.status_code == 401
    assert cutoff_calls == [(user_id, 2_000_000_000.100)]


@pytest.mark.regression
@pytest.mark.asyncio
async def test_mfa_challenge_issued_after_cutoff_is_accepted(monkeypatch):
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

    async def not_revoked(_jti):
        return False

    cutoff_calls = []

    async def postdates_cutoff(uid, iat):
        cutoff_calls.append((uid, iat))
        return False

    monkeypatch.setattr(
        mfa,
        "decode_token",
        lambda *_args, **_kwargs: {
            "sub": str(user_id),
            "jti": "new-challenge-jti",
            "iat": 2_000_000_000.750,
            "exp": 2_000_000_300,
        },
    )
    monkeypatch.setattr(mfa, "is_jti_revoked", not_revoked)
    monkeypatch.setattr(mfa, "is_token_before_cutoff", postdates_cutoff)

    resolved = await mfa._user_from_mfa_token(Session(), "challenge", "mfa_challenge")

    assert resolved is user
    assert cutoff_calls == [(user_id, 2_000_000_000.750)]
