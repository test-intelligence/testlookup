"""Every password-accepting schema carries the same length cap.

``UserCreate`` was capped at 128 (bcrypt DoS, audit item S4) but
``ChangePasswordRequest`` / ``FirstTimeResetRequest`` were not — so the exact
same unbounded value that registration rejected sailed into ``bcrypt.hashpw``
via /auth/change-password and /auth/first-time-reset.

The cap is a resource guard, not a password policy: no complexity rules here.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas import (
    MAX_PASSWORD_LENGTH,
    ChangePasswordRequest,
    FirstTimeResetRequest,
    LoginRequest,
    UserCreate,
)

_OVER = "a" * (MAX_PASSWORD_LENGTH + 1)
_AT_CAP = "a" * MAX_PASSWORD_LENGTH


def test_cap_is_the_documented_value():
    assert MAX_PASSWORD_LENGTH == 128


def test_registration_cap_still_holds():
    with pytest.raises(ValidationError):
        UserCreate(
            email="a@b.com", username="abc", password=_OVER
        )


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(
            lambda pw: ChangePasswordRequest(current_password="old-pass", new_password=pw),
            id="change-password-new",
        ),
        pytest.param(
            lambda pw: ChangePasswordRequest(current_password=pw, new_password="new-pass"),
            id="change-password-current",
        ),
        pytest.param(
            lambda pw: FirstTimeResetRequest(new_password=pw, confirm_password="new-pass"),
            id="first-time-reset-new",
        ),
        pytest.param(
            lambda pw: FirstTimeResetRequest(new_password="new-pass", confirm_password=pw),
            id="first-time-reset-confirm",
        ),
        pytest.param(
            lambda pw: LoginRequest(username="u", password=pw),
            id="login",
        ),
    ],
)
def test_over_cap_password_is_rejected(build):
    with pytest.raises(ValidationError):
        build(_OVER)


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(
            lambda pw: ChangePasswordRequest(current_password="old-pass", new_password=pw),
            id="change-password",
        ),
        pytest.param(
            lambda pw: FirstTimeResetRequest(new_password=pw, confirm_password=pw),
            id="first-time-reset",
        ),
        pytest.param(lambda pw: LoginRequest(username="u", password=pw), id="login"),
        pytest.param(
            lambda pw: UserCreate(email="a@b.com", username="abc", password=pw),
            id="registration",
        ),
    ],
)
def test_exactly_at_cap_is_accepted(build):
    """Behaviour-preserving: the cap rejects only what is strictly longer."""
    assert build(_AT_CAP) is not None


def test_minimum_length_is_unchanged_on_the_new_password_fields():
    with pytest.raises(ValidationError):
        ChangePasswordRequest(current_password="old-pass", new_password="short")
    with pytest.raises(ValidationError):
        FirstTimeResetRequest(new_password="short", confirm_password="short")


def test_current_password_has_no_minimum():
    """Rejecting a short ``current_password`` at the schema would leak that no
    short password can be the current one — the check belongs against the hash."""
    assert ChangePasswordRequest(current_password="x", new_password="long-enough-pw")
