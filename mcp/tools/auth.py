"""Tools: login, health_check."""

from __future__ import annotations

import json

import client as api  # type: ignore[import]
from config import settings  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def login(username: str, password: str) -> str:
        """
        Authenticate against TestLookup and cache the JWT token for this session.
        Call this first if the server was started without TESTLOOKUP_USERNAME/PASSWORD env vars,
        or to switch users mid-session.
        """
        import client as _api  # local import so we can mutate module state
        _api._access_token = None

        from config import settings as _s  # type: ignore[import]
        # Temporarily override credentials for this call
        resp = await _api._get_client().post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        if resp.status_code != 200:
            return f"Login failed (HTTP {resp.status_code}): {resp.text}"

        data = resp.json()
        _api._access_token = data["access_token"]
        return (
            f"Logged in as **{username}**.\n"
            f"Token expires in: {data.get('expires_in', 'unknown')}s\n"
            f"Token type: {data.get('token_type', 'bearer')}"
        )

    @mcp.tool()
    async def health_check() -> str:
        """
        Verify that the TestLookup backend is reachable and return its status,
        version, environment, and configured LLM provider.
        """
        try:
            data = await api.get("/health")
        except Exception as exc:
            return f"Backend unreachable at {settings.api_url}: {exc}"

        lines = [
            f"**Status:** {data.get('status', 'unknown')}",
            f"**Version:** {data.get('version', 'unknown')}",
            f"**Environment:** {data.get('env', 'unknown')}",
            f"**LLM Provider:** {data.get('llm_provider', 'unknown')}",
            f"**Offline Mode:** {data.get('offline_mode', False)}",
            f"**API URL:** {settings.api_url}",
        ]
        return "\n".join(lines)
