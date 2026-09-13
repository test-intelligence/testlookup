"""
Async HTTP client for the TestLookup backend.

Handles JWT authentication automatically:
- Authenticates on first request (if credentials are configured)
- Re-authenticates transparently on 401
- Falls back to unauthenticated requests if no credentials are set
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from config import settings  # type: ignore[import]

_stdio_access_token: Optional[str] = None
_client: Optional[httpx.AsyncClient] = None
_auth_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=settings.api_url,
            timeout=httpx.Timeout(settings.request_timeout),
        )
    return _client


def _get_auth_client() -> httpx.AsyncClient:
    """Return a bounded, short-timeout client for public authentication paths."""
    global _auth_client
    if _auth_client is None or _auth_client.is_closed:
        _auth_client = httpx.AsyncClient(
            base_url=settings.api_url,
            timeout=httpx.Timeout(settings.auth_timeout),
            limits=httpx.Limits(
                max_connections=settings.auth_max_connections,
                max_keepalive_connections=min(20, settings.auth_max_connections),
            ),
        )
    return _auth_client


async def authenticate() -> Optional[str]:
    """Login for the single-client stdio transport and cache its access token."""
    global _stdio_access_token
    if not settings.username or not settings.password:
        return None

    client = _get_client()
    # Backend expects OAuth2 form-encoded data, not JSON
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": settings.username, "password": settings.password},
    )
    resp.raise_for_status()
    _stdio_access_token = resp.json()["access_token"]
    return _stdio_access_token


def _request_access_token() -> Optional[str]:
    """Return the SDK-validated token for the current network request, if any."""
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token

        access = get_access_token()
    except (ImportError, LookupError):
        return None
    return access.token if access is not None else None


async def verify_bearer_token(token: str) -> Optional[dict[str, Any]]:
    """Ask the backend to validate a network caller without using fallback auth."""
    try:
        resp = await _get_auth_client().get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        profile = resp.json()
    except ValueError:
        return None
    return profile if isinstance(profile, dict) else None


async def backend_ready() -> bool:
    """Check the authentication authority used by network MCP handshakes."""
    try:
        response = await _get_auth_client().get("/health/ready")
    except httpx.HTTPError:
        return False
    return response.status_code == 200


async def _auth_context() -> tuple[dict[str, str], bool]:
    """Return headers plus whether they belong to an authenticated MCP caller."""
    request_token = _request_access_token()
    if request_token is not None:
        return {"Authorization": f"Bearer {request_token}"}, True

    global _stdio_access_token
    if _stdio_access_token is None and settings.username:
        await authenticate()
    headers = (
        {"Authorization": f"Bearer {_stdio_access_token}"}
        if _stdio_access_token
        else {}
    )
    return headers, False


async def _auth_headers() -> dict[str, str]:
    headers, _ = await _auth_context()
    return headers


def _clean_params(params: Optional[dict]) -> Optional[dict]:
    """Strip None values so they are not sent as query params."""
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None}


async def get(path: str, params: Optional[dict] = None) -> Any:
    client = _get_client()
    headers, caller_scoped = await _auth_context()
    resp = await client.get(path, params=_clean_params(params), headers=headers)

    if resp.status_code == 401 and not caller_scoped and settings.username:
        global _stdio_access_token
        _stdio_access_token = None
        headers, _ = await _auth_context()
        resp = await client.get(path, params=_clean_params(params), headers=headers)

    resp.raise_for_status()
    return resp.json()


async def post(
    path: str,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
    extra_headers: Optional[dict] = None,
) -> Any:
    """POST with the caller's auth. ``extra_headers`` (e.g. Idempotency-Key)
    are sent too, but can never override the auth headers."""
    client = _get_client()
    headers, caller_scoped = await _auth_context()
    resp = await client.post(
        path, json=json_body, params=_clean_params(params), headers={**(extra_headers or {}), **headers},
    )

    if resp.status_code == 401 and not caller_scoped and settings.username:
        global _stdio_access_token
        _stdio_access_token = None
        headers, _ = await _auth_context()
        resp = await client.post(
            path, json=json_body, params=_clean_params(params), headers={**(extra_headers or {}), **headers},
        )

    resp.raise_for_status()
    # Handle 204 No Content responses (empty body) gracefully.
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


async def patch(path: str, json_body: Optional[dict] = None) -> Any:
    """PATCH helper — identical 401-retry semantics as ``post``."""
    client = _get_client()
    headers, caller_scoped = await _auth_context()
    resp = await client.patch(path, json=json_body, headers=headers)

    if resp.status_code == 401 and not caller_scoped and settings.username:
        global _stdio_access_token
        _stdio_access_token = None
        headers, _ = await _auth_context()
        resp = await client.patch(path, json=json_body, headers=headers)

    resp.raise_for_status()
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


async def put(path: str, json_body: Optional[dict] = None) -> Any:
    """PUT helper — used by config upserts (feature flags, quotas)."""
    client = _get_client()
    headers, caller_scoped = await _auth_context()
    resp = await client.put(path, json=json_body, headers=headers)

    if resp.status_code == 401 and not caller_scoped and settings.username:
        global _stdio_access_token
        _stdio_access_token = None
        headers, _ = await _auth_context()
        resp = await client.put(path, json=json_body, headers=headers)

    resp.raise_for_status()
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


def error_payload(exc: Exception) -> dict[str, Any]:
    """Normalize an HTTP/client error into a structured result dict.

    Write tools return dicts (never bare strings) so agents can branch on
    ``ok`` and surface the backend's ``detail`` message — which carries the
    server-side RBAC verdict (403), state-machine conflicts (409), and
    validation errors (422) — instead of a stack trace.
    """
    payload: dict[str, Any] = {"ok": False, "error": str(exc)}
    if isinstance(exc, httpx.HTTPStatusError):
        payload["status_code"] = exc.response.status_code
        try:
            detail = exc.response.json().get("detail")
        except Exception:
            detail = exc.response.text[:500] or None
        if detail is not None:
            payload["detail"] = detail
    return payload


async def delete(path: str) -> None:
    """DELETE helper — most endpoints return 204 No Content."""
    client = _get_client()
    headers, caller_scoped = await _auth_context()
    resp = await client.delete(path, headers=headers)

    if resp.status_code == 401 and not caller_scoped and settings.username:
        global _stdio_access_token
        _stdio_access_token = None
        headers, _ = await _auth_context()
        resp = await client.delete(path, headers=headers)

    resp.raise_for_status()
    return None
