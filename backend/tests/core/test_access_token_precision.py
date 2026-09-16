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
