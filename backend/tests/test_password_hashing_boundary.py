"""bcrypt's 72-byte boundary — real functions, no mocks.

bcrypt < 5.0 truncated silently past 72 bytes; 5.0 RAISES ValueError instead.
Left unhandled that is a 500 on *unauthenticated* ``POST /auth/login`` for any
password over 72 bytes, and on register / change-password / first-time reset.

``core.security`` truncates explicitly, which closes the 500 and preserves the
pre-5.0 semantics so credentials created while bcrypt truncated silently still
verify — rejecting instead would lock those users out of their own accounts.

Deliberately a separate module: the sibling hardening suites patch
``verify_password`` at import, and these tests must exercise the real thing.
"""
from __future__ import annotations

from app.core.security import get_password_hash, verify_password


def test_long_password_hashes_and_verifies_instead_of_raising():
    long_password = "a" * 200
    hashed = get_password_hash(long_password)  # must not raise
    assert verify_password(long_password, hashed) is True


def test_truncation_semantics_are_bcrypts_own():
    """Anything sharing the first 72 bytes verifies — documented bcrypt
    behaviour, inherited by truncating rather than something we invented."""
    hashed = get_password_hash("a" * 200)
    assert verify_password("a" * 72, hashed) is True
    assert verify_password("a" * 71 + "b", hashed) is False


def test_multibyte_password_over_the_byte_cap_does_not_raise():
    """The cap is BYTES, so a multi-byte character can be cut mid-sequence.
    That is fine: the value only has to be deterministic, never decoded back."""
    pw = "é" * 50  # 100 UTF-8 bytes
    hashed = get_password_hash(pw)
    assert verify_password(pw, hashed) is True


def test_short_passwords_are_completely_unaffected():
    hashed = get_password_hash("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong horse battery staple", hashed) is False
