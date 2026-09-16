"""Password changes and session issuance must serialize on the user row.

Without this lock, a login can verify the old password while a reset is still
uncommitted, then mint a session after the reset cutoff was written.  The new
session survives the operation that was meant to revoke every prior session.
"""
from __future__ import annotations

import ast
import inspect

import pytest


def _calls_method(function, method: str) -> bool:
    tree = ast.parse(inspect.getsource(function))
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == method
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
