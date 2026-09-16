from datetime import datetime, timezone

from app.core import security


def test_access_token_iat_preserves_subsecond_precision(monkeypatch):
    issued_at = datetime(2026, 9, 16, 15, 30, 0, 750000, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return issued_at

    monkeypatch.setattr(security, "datetime", FixedDateTime)

    payload = security.decode_token(security.create_access_token("precision-user"))

    assert payload["iat"] == issued_at.timestamp()
    assert payload["iat"] != int(issued_at.timestamp())


def test_mfa_interstitial_iat_preserves_subsecond_precision(monkeypatch):
    issued_at = datetime.now(timezone.utc).replace(microsecond=750000)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return issued_at

    monkeypatch.setattr(security, "datetime", FixedDateTime)

    encoded, _jti, _ttl = security.create_mfa_token(
        "precision-user", security.MFA_CHALLENGE_TOKEN_TYPE
    )
    payload = security.decode_token(encoded, expected_type=security.MFA_CHALLENGE_TOKEN_TYPE)

    assert payload["iat"] == issued_at.timestamp()
    assert payload["iat"] != int(issued_at.timestamp())
