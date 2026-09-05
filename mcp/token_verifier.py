"""Bearer-token verification for network MCP transports.

The TestLookup API remains the authentication authority.  The MCP server does
not mint credentials and deliberately does not cache verification: every SSE
request is checked against the backend so revocation and account deactivation
take effect without waiting for a local TTL.
"""

from __future__ import annotations

import logging
from typing import Any

import jwt
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

import client as api
from config import settings


logger = logging.getLogger(__name__)


def _validated_claims(token: str, profile: dict[str, Any]) -> dict[str, Any] | None:
    """Read identity claims only after the backend accepted ``token``.

    Signature, expiry, token type, revocation, and active-user checks belong to
    ``/auth/me``.  Decoding without signature verification here only extracts
    the already-validated token's stable session key and expiry for the MCP
    SDK's transport-level session binding.
    """
    try:
        claims = jwt.decode(
            token,
            options={"verify_signature": False, "verify_exp": False},
            algorithms=["HS256"],
        )
    except jwt.PyJWTError:
        return None

    subject = claims.get("sub")
    jti = claims.get("jti")
    expires_at = claims.get("exp")
    if (
        not isinstance(subject, str)
        or subject != str(profile.get("id", ""))
        or not isinstance(jti, str)
        or not jti
        or not isinstance(expires_at, int)
        or claims.get("type") != "access"
    ):
        return None
    return {"subject": subject, "jti": jti, "expires_at": expires_at}


class TestLookupTokenVerifier:
    """Validate a caller bearer token through TestLookup's existing auth API."""

    async def verify_token(self, token: str) -> AccessToken | None:
        profile = await api.verify_bearer_token(token)
        if not profile or profile.get("is_active") is not True:
            return None

        identity = _validated_claims(token, profile)
        if identity is None:
            logger.warning("MCP bearer token passed /auth/me but had inconsistent claims")
            return None

        return AccessToken(
            token=token,
            # The jti makes one bearer credential the owner of one SSE session.
            # A refreshed token must reconnect instead of taking over a session.
            client_id=identity["jti"],
            subject=identity["subject"],
            expires_at=identity["expires_at"],
            scopes=[],
            claims={
                "username": str(profile.get("username", "")),
                "role": str(profile.get("role", "")),
            },
        )


def build_auth_settings() -> AuthSettings:
    """Describe the backend as issuer while using it for direct validation.

    TestLookup does not currently expose a complete OAuth authorization-server
    discovery flow, so no protected-resource metadata URL is advertised.  MCP
    clients provide an access token obtained from TestLookup itself.
    """
    issuer = AnyHttpUrl(f"{settings.api_url.rstrip('/')}/api/v1/auth")
    return AuthSettings(
        issuer_url=issuer,
        resource_server_url=None,
        required_scopes=[],
    )
