"""The backend's JWTs are signed and verified by PyJWT, not python-jose (2026-10-12).

python-jose <= 3.5.0 has an unfixed algorithm-confusion bypass
(GHSA-3qf3-8w2g-rqmx, critical) and is unmaintained. The swap must change the
library and nothing a client can see:

* every token type the app issues -- access, refresh, both MFA interstitials --
  still round-trips with the same claims;
* a token python-jose minted before the deploy still decodes, so nobody is
  signed out by the upgrade;
* PyJWT checks one thing jose never did: an ``iat`` later than "now". ``iat`` is
  minted from PostgreSQL's clock (``auth_session_tokens``), so with PyJWT's
  default zero leeway a database clock even a second ahead of the API pod
  would 401 every fresh sign-in. ``decode_token`` keeps jose's rule instead
  (``iat`` must be a number), and ``exp`` still bounds the token;
* the error names callers catch keep their meaning (``ExpiredSignatureError``
  is still a ``JWTError``, which deps.get_current_user relies on to label
  expiry apart from forgery);
* the backend imports with jose blocked outright, so nothing still needs it.

The repo-wide guard against jose coming back (manifests, imports, test stubs)
is the quality gate's ``deps.no-python-jose``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest

from app.core import security
from app.core.config import settings
from app.core.security import (
    MFA_CHALLENGE_TOKEN_TYPE,
    MFA_ENROLLMENT_TOKEN_TYPE,
    ExpiredSignatureError,
    JWTError,
    create_access_token,
    create_mfa_token,
    create_refresh_token,
    decode_token,
)

BACKEND = Path(__file__).resolve().parents[2]

# Minted by python-jose 3.5.0 with this secret (2026-10-12), exp 2099-01-01:
#   jwt.encode({"sub": SUB, "iat": <2026-10-11T12:00:00.123456Z as a float>,
#               "exp": datetime(2099, 1, 1), "type": "access", "jti": ...}, ...)
JOSE_SECRET = "jose-era-test-secret-0123456789abcdef"
JOSE_SUB = "7b0c6a2e-4f3d-4a59-9d6e-0d5b8f1a2c33"
JOSE_ACCESS = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiI3YjBjNmEyZS00ZjNkLTRhNTktOWQ2ZS0wZDViOGYxYTJjMzMiLCJpYXQiOjE3OTE3MjAwMDAu"
    "MTIzNDU2LCJleHAiOjQwNzA5MDg4MDAsInR5cGUiOiJhY2Nlc3MiLCJqdGkiOiI1ZjFjMmQzZTRiNWE2OTc4"
    "ODc5NmE1YjRjM2QyZTFmMCJ9."
    "sLb15PX09XTlHGuHgONeKD6S2pswWb5i2LISv1bVVvg"
)
JOSE_REFRESH = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiI3YjBjNmEyZS00ZjNkLTRhNTktOWQ2ZS0wZDViOGYxYTJjMzMiLCJleHAiOjQwNzA5MDg4MDAs"
    "InR5cGUiOiJyZWZyZXNoIiwianRpIjoicTJ3M2U0cjV0Nnk3dThpOW8wcDFhMnMzZDRmNWc2aDcifQ."
    "uRWWnTSYPTc814CI06HcfTUA443NS8VpGMGu8mYxRD0"
)


@pytest.fixture(autouse=True)
def _hs256(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "pyjwt-regression-secret-0123456789abcdef")
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _hand_signed(header: dict, payload: dict, secret: str) -> str:
    """A token built without any JWT library, so the test controls every byte."""
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}"
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(sig)}"


# ── the library ─────────────────────────────────────────────────────────────

def test_the_error_names_are_pyjwts_and_keep_their_hierarchy() -> None:
    assert JWTError is jwt.PyJWTError
    assert ExpiredSignatureError is jwt.ExpiredSignatureError
    # deps.get_current_user catches ExpiredSignatureError first, then JWTError.
    assert issubclass(ExpiredSignatureError, JWTError)
    assert issubclass(jwt.InvalidKeyError, JWTError)
    assert security.jwt is jwt


def test_the_backend_imports_with_python_jose_blocked() -> None:
    """A fresh interpreter in which ``import jose`` raises: every module that
    used it must still import, and nothing may have loaded jose."""
    code = (
        "import importlib.abc, sys\n"
        "class _Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path, target=None):\n"
        "        if name == 'jo' 'se' or name.startswith('jo' 'se.'):\n"
        "            raise ImportError('python-jose is blocked in this test')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n"
        "import app.core.security, app.core.deps\n"
        "import app.routers.auth, app.routers.mfa, app.routers.live, app.routers.stream\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'jo' 'se'))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert proc.stdout.strip().splitlines()[-1] == "[]"


# ── every token type round-trips ────────────────────────────────────────────

def test_an_access_token_round_trips_with_its_claims() -> None:
    subject = uuid.uuid4()
    issued = datetime.now(timezone.utc).replace(microsecond=654321)
    token = create_access_token(subject, issued_at=issued)
    claims = decode_token(token, expected_type="access")
    assert claims["sub"] == str(subject)
    assert claims["type"] == "access"
    assert isinstance(claims["jti"], str) and len(claims["jti"]) == 32
    # Sub-second iat survives: the revocation cutoff compares at that precision.
    assert claims["iat"] == pytest.approx(issued.timestamp(), abs=1e-6)
    assert isinstance(claims["exp"], int)
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


def test_a_refresh_token_round_trips_with_its_jti() -> None:
    token, jti, expires_at = create_refresh_token("user-1")
    claims = decode_token(token, expected_type="refresh")
    assert claims["sub"] == "user-1" and claims["jti"] == jti
    assert claims["exp"] == int(expires_at.timestamp())


@pytest.mark.parametrize("token_type", [MFA_CHALLENGE_TOKEN_TYPE, MFA_ENROLLMENT_TOKEN_TYPE])
def test_mfa_interstitial_tokens_round_trip_and_are_not_access_tokens(token_type: str) -> None:
    token, jti, ttl = create_mfa_token(uuid.uuid4(), token_type)
    assert decode_token(token, expected_type=token_type)["jti"] == jti
    with pytest.raises(JWTError):
        decode_token(token, expected_type="access")


def test_tokens_python_jose_minted_before_the_upgrade_still_decode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", JOSE_SECRET)
    access = decode_token(JOSE_ACCESS, expected_type="access")
    assert access["sub"] == JOSE_SUB
    assert access["iat"] == pytest.approx(
        datetime(2026, 10, 11, 12, 0, 0, 123456, tzinfo=timezone.utc).timestamp(), abs=1e-6
    )
    refresh = decode_token(JOSE_REFRESH, expected_type="refresh")
    assert refresh["jti"] == "q2w3e4r5t6y7u8i9o0p1a2s3d4f5g6h7"


# ── claim rules ─────────────────────────────────────────────────────────────

def test_an_iat_from_a_database_clock_ahead_of_this_pod_is_accepted() -> None:
    """The regression PyJWT's defaults would have shipped: a fresh sign-in
    401ing because PostgreSQL's clock_timestamp() ran ahead of the API pod."""
    ahead = datetime.now(timezone.utc) + timedelta(seconds=90)
    token = create_access_token(uuid.uuid4(), issued_at=ahead)
    assert decode_token(token, expected_type="access")["type"] == "access"


def test_a_non_numeric_iat_is_rejected_as_jose_rejected_it() -> None:
    exp = int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp())
    token = _hand_signed(
        {"alg": "HS256", "typ": "JWT"},
        {"sub": "u", "type": "access", "jti": "j", "exp": exp, "iat": "yesterday"},
        settings.JWT_SECRET_KEY,
    )
    with pytest.raises(JWTError):
        decode_token(token)


def test_an_expired_token_raises_expired_signature_error() -> None:
    token = create_access_token("u", expires_delta=timedelta(seconds=-5))
    with pytest.raises(ExpiredSignatureError):
        decode_token(token)


@pytest.mark.parametrize("bad", ["", "not-a-jwt", "a.b.c", "eyJhbGciOiJIUzI1NiJ9.e30."])
def test_malformed_tokens_raise_jwt_error(bad: str) -> None:
    with pytest.raises(JWTError):
        decode_token(bad)


# ── the token never chooses its own algorithm ───────────────────────────────

def test_an_unsigned_alg_none_token_is_refused() -> None:
    exp = int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp())
    header = _b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = _b64(json.dumps({"sub": "admin", "type": "access", "jti": "j", "exp": exp}).encode())
    token = f"{header}.{body}."
    with pytest.raises(JWTError):
        decode_token(token)


def test_a_token_whose_header_names_another_algorithm_is_refused() -> None:
    exp = int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp())
    payload = {"sub": "admin", "type": "access", "jti": "j", "exp": exp}
    forged = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS512")
    with pytest.raises(JWTError):
        decode_token(forged)


def test_a_public_key_is_never_accepted_as_the_hmac_secret(monkeypatch) -> None:
    """GHSA-3qf3-8w2g-rqmx's shape: an HMAC 'secret' that is really a public
    key. PyJWT refuses to use PEM/SSH key material as an HMAC secret."""
    pem = "-----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE\n-----END PUBLIC KEY-----\n"
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", pem)
    with pytest.raises(JWTError):
        create_access_token("u")
