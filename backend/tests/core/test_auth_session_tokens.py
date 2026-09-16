from datetime import datetime, timezone

import pytest


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _Session:
    def __init__(self, value):
        self.value = value
        self.statements = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        return _Result(self.value)


@pytest.mark.asyncio
async def test_access_session_jwt_uses_postgres_issued_at():
    from app.core.security import decode_token
    from app.services.auth_session_tokens import issue_access_jwt

    issued_at = datetime(2033, 5, 18, 3, 33, 20, 750000, tzinfo=timezone.utc)
    db = _Session(issued_at)

    payload = decode_token(await issue_access_jwt(db, "db-clock-user"), expected_type="access")

    assert payload["iat"] == issued_at.timestamp()
    assert "clock_timestamp()" in db.statements[0].lower()


@pytest.mark.asyncio
async def test_mfa_session_jwt_uses_postgres_issued_at():
    from app.core.security import MFA_CHALLENGE_TOKEN_TYPE, decode_token
    from app.services.auth_session_tokens import issue_mfa_jwt

    issued_at = datetime(2033, 5, 18, 3, 33, 20, 750000, tzinfo=timezone.utc)
    db = _Session(issued_at)

    encoded, _jti, _ttl = await issue_mfa_jwt(
        db, "db-clock-user", MFA_CHALLENGE_TOKEN_TYPE
    )
    payload = decode_token(encoded, expected_type=MFA_CHALLENGE_TOKEN_TYPE)

    assert payload["iat"] == issued_at.timestamp()
    assert "clock_timestamp()" in db.statements[0].lower()
