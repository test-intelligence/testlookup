"""Tools: login, health_check."""

from __future__ import annotations

import client as api  # type: ignore[import]
from config import settings  # type: ignore[import]


def _render_health(data: dict) -> str:
    """Render a ``/health/details`` payload as a human-readable status block.

    Kept pure (no I/O) so the formatting can be unit-tested without a live
    backend. The endpoint reports per-dependency status under the ``checks``
    key — NOT ``dependencies`` — and each value is a dict shaped like
    ``{"status": "ok", "latency_ms": 3}``, so we surface the nested
    ``status`` rather than stringifying the whole dict. Degrades gracefully
    on a missing/malformed ``checks`` block (no dependency section).
    """
    lines = [
        f"**Status:** {data.get('status', 'unknown')}",
        f"**Version:** {data.get('version', 'unknown')}",
        f"**Environment:** {data.get('env', 'unknown')}",
    ]
    uptime = data.get("uptime_seconds")
    if isinstance(uptime, (int, float)) and not isinstance(uptime, bool):
        lines.append(f"**Uptime:** {int(uptime)}s")
    checks = data.get("checks")
    if isinstance(checks, dict) and checks:
        lines.append("**Dependencies:**")
        for dep_name, dep_info in checks.items():
            dep_status = (
                dep_info.get("status", "unknown")
                if isinstance(dep_info, dict)
                else dep_info
            )
            lines.append(f"  - {dep_name}: {dep_status}")
    return "\n".join(lines)


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def login(username: str, password: str) -> str:
        """
        Authenticate a local stdio session and cache its JWT token.
        Network sessions already carry a request-scoped bearer token and cannot
        use this tool to switch or replace identity.
        """
        import client as _api  # local import so we can mutate stdio state
        if _api._request_access_token() is not None:
            return (
                "Login is available only over the local stdio transport. "
                "Network MCP sessions must reconnect with their own TestLookup bearer token."
            )
        _api._stdio_access_token = None

        # Backend expects OAuth2 form-encoded data, not JSON
        resp = await _api._get_client().post(
            "/api/v1/auth/login",
            data={"username": username, "password": password},
        )
        if resp.status_code != 200:
            return f"Login failed (HTTP {resp.status_code}): {resp.text}"

        data = resp.json()
        _api._stdio_access_token = data["access_token"]
        return (
            f"Logged in as **{username}**.\n"
            f"Token expires in: {data.get('expires_in', 'unknown')}s\n"
            f"Token type: {data.get('token_type', 'bearer')}"
        )

    @mcp.tool()
    async def health_check() -> str:
        """
        Verify that the TestLookup backend is reachable and return its status,
        version, environment, uptime, and per-dependency health.
        """
        try:
            data = await api.get("/health/details")
        except Exception as exc:
            return f"Backend unreachable at {settings.api_url}: {exc}"

        return f"{_render_health(data)}\n**API URL:** {settings.api_url}"
