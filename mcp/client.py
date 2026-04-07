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

_access_token: Optional[str] = None
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=settings.api_url,
            timeout=httpx.Timeout(settings.request_timeout),
        )
    return _client


async def authenticate() -> Optional[str]:
    """Login with configured credentials and cache the access token."""
    global _access_token
    if not settings.username or not settings.password:
        return None

    client = _get_client()
    # Backend expects OAuth2 form-encoded data, not JSON
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": settings.username, "password": settings.password},
    )
    resp.raise_for_status()
    _access_token = resp.json()["access_token"]
    return _access_token


async def _auth_headers() -> dict[str, str]:
    global _access_token
    if _access_token is None and settings.username:
        await authenticate()
    return {"Authorization": f"Bearer {_access_token}"} if _access_token else {}


def _clean_params(params: Optional[dict]) -> Optional[dict]:
    """Strip None values so they are not sent as query params."""
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None}


async def get(path: str, params: Optional[dict] = None) -> Any:
    client = _get_client()
    headers = await _auth_headers()
    resp = await client.get(path, params=_clean_params(params), headers=headers)

    if resp.status_code == 401 and settings.username:
        global _access_token
        _access_token = None
        headers = await _auth_headers()
        resp = await client.get(path, params=_clean_params(params), headers=headers)

    resp.raise_for_status()
    return resp.json()


async def post(path: str, json_body: Optional[dict] = None) -> Any:
    client = _get_client()
    headers = await _auth_headers()
    resp = await client.post(path, json=json_body, headers=headers)

    if resp.status_code == 401 and settings.username:
        global _access_token
        _access_token = None
        headers = await _auth_headers()
        resp = await client.post(path, json=json_body, headers=headers)

    resp.raise_for_status()
    return resp.json()
