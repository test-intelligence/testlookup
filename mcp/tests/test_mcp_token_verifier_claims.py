"""The SSE bearer verifier refuses every token whose claims are not an access token's (GAP-E08).

``test_mcp_principal_isolation.py`` covers subject binding, a backend ``None``,
an inactive user, a non-JWT and an auth timeout. The other claim checks in
``token_verifier._validated_claims`` and the ``is_active is not True`` check
were held by nothing: deleting ``or claims.get("type") != "access"`` left the
suite green, so a refresh token would open a session. ``/auth/me`` should
refuse it first; this is the second line of defence.

Every refusal case changes ONE thing from a token and profile that the same
module shows are accepted, so a refusal can only come from that one change.
``verify_bearer_token`` is driven through an ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import jwt
import pytest

MCP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCP_DIR))

import client  # noqa: E402
from token_verifier import TestLookupTokenVerifier  # noqa: E402

SECRET = "ci-mcp-jwt-secret-test-only-at-least-32-bytes"
PROFILE = {
    "id": "alice-id",
    "username": "alice",
    "role": "QA_ENGINEER",
    "is_active": True,
}
BASE_CLAIMS = {
    "sub": "alice-id",
    "jti": "alice-jti",
    "exp": 2_000_000_000,
    "type": "access",
}
_DROP = object()


def _token(**changes) -> str:
    claims = dict(BASE_CLAIMS)
    for key, value in changes.items():
        if value is _DROP:
            claims.pop(key, None)
        else:
            claims[key] = value
    return jwt.encode(claims, SECRET, algorithm="HS256")


def _verify(monkeypatch, token, profile):
    async def _backend(seen_token):
        assert seen_token == token
        return profile

    monkeypatch.setattr(client, "verify_bearer_token", _backend)
    return asyncio.run(TestLookupTokenVerifier().verify_token(token))


def test_the_base_access_token_and_profile_are_accepted(monkeypatch):
    verified = _verify(monkeypatch, _token(), dict(PROFILE))

    assert verified is not None
    assert (verified.subject, verified.client_id, verified.expires_at) == (
        "alice-id",
        "alice-jti",
        2_000_000_000,
    )


CLAIM_CASES = {
    "a refresh token": {"type": "refresh"},
    "no token type": {"type": _DROP},
    "no jti": {"jti": _DROP},
    "an empty jti": {"jti": ""},
    "a non-string jti": {"jti": 12345},
    "a string exp": {"exp": "2000000000"},
    "a float exp": {"exp": 2_000_000_000.5},
    "no exp": {"exp": _DROP},
    "no subject": {"sub": _DROP},
    "a non-string subject": {"sub": 42},
}


@pytest.mark.parametrize("case", list(CLAIM_CASES))
def test_a_token_with_wrong_claims_is_refused(monkeypatch, case):
    assert _verify(monkeypatch, _token(**CLAIM_CASES[case]), dict(PROFILE)) is None, (
        f"a bearer with {case} passed /auth/me and must still be refused"
    )


PROFILE_CASES = {
    "is_active missing": {"is_active": _DROP},
    'is_active the string "true"': {"is_active": "true"},
    "is_active 1": {"is_active": 1},
    "is_active None": {"is_active": None},
}


@pytest.mark.parametrize("case", list(PROFILE_CASES))
def test_a_profile_that_is_not_strictly_active_is_refused(monkeypatch, case):
    profile = dict(PROFILE)
    for key, value in PROFILE_CASES[case].items():
        if value is _DROP:
            profile.pop(key)
        else:
            profile[key] = value

    assert _verify(monkeypatch, _token(), profile) is None, (
        f"a profile with {case} must be refused"
    )


# ── verify_bearer_token against a fake /auth/me ──────────────────────────────


def _me(monkeypatch, response: httpx.Response):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    async def _go():
        http = httpx.AsyncClient(
            base_url="http://testlookup.example.test",
            transport=httpx.MockTransport(handler),
        )
        monkeypatch.setattr(client, "_get_auth_client", lambda: http)
        try:
            return await client.verify_bearer_token("ci-bearer-test-only")
        finally:
            await http.aclose()

    result = asyncio.run(_go())
    assert [(r.method, r.url.path, r.headers.get("authorization")) for r in seen] == [
        ("GET", "/api/v1/auth/me", "Bearer ci-bearer-test-only")
    ]
    return result


def test_auth_me_profile_is_returned(monkeypatch):
    assert _me(monkeypatch, httpx.Response(200, json=PROFILE)) == PROFILE


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"detail": "Could not validate credentials"}),
        httpx.Response(403, json={"detail": "Inactive user"}),
        httpx.Response(
            200,
            text="<html><body>proxy login</body></html>",
            headers={"content-type": "text/html"},
        ),
        httpx.Response(200, json=[PROFILE]),
        httpx.Response(200, json="alice"),
    ],
    ids=["401", "403", "200 html", "200 json list", "200 json string"],
)
def test_auth_me_refusals_and_odd_bodies_give_no_profile(monkeypatch, response):
    assert _me(monkeypatch, response) is None


def test_a_401_from_auth_me_refuses_the_session_end_to_end(monkeypatch):
    token = _token()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Token revoked"})

    async def _go():
        http = httpx.AsyncClient(
            base_url="http://testlookup.example.test",
            transport=httpx.MockTransport(handler),
        )
        monkeypatch.setattr(client, "_get_auth_client", lambda: http)
        try:
            return await TestLookupTokenVerifier().verify_token(token)
        finally:
            await http.aclose()

    assert asyncio.run(_go()) is None
