"""Shared HTTP client — handles auth injection, retries, and error mapping."""
import httpx
from typing import Any, Optional

from testlookup_cli.config import get_profile, save_profile
from testlookup_cli.errors import CLIError, map_http_error, map_connection_error, EXIT_AUTH


def _build_headers(profile: dict) -> dict[str, str]:
    """Build auth headers from the active profile."""
    auth_type = profile.get("auth_type", "jwt")
    if auth_type == "api_key" and profile.get("api_key"):
        return {"X-API-Key": profile["api_key"]}
    if profile.get("access_token"):
        return {"Authorization": f"Bearer {profile['access_token']}"}
    return {}


async def request(
    method: str,
    path: str,
    *,
    profile_name: Optional[str] = None,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    timeout: float = 30.0,
) -> Any:
    """Make an authenticated HTTP request to the TestLookup API."""
    profile = get_profile(profile_name)
    base_url = profile.get("url", "http://localhost:8000").rstrip("/")
    url = f"{base_url}{path}"
    headers = _build_headers(profile)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, headers=headers, params=params, json=json_body)

            # Auto-refresh JWT on 401 if refresh token available
            if resp.status_code == 401 and profile.get("auth_type") == "jwt" and profile.get("refresh_token"):
                refreshed = await _try_refresh(client, base_url, profile, profile_name)
                if refreshed:
                    headers = _build_headers(profile)
                    resp = await client.request(method, url, headers=headers, params=params, json=json_body)

            if resp.status_code >= 400:
                detail = ""
                try:
                    detail = resp.json().get("detail", "")
                except Exception:
                    pass
                raise map_http_error(resp.status_code, str(detail))

            if resp.status_code == 204:
                return None
            return resp.json()
    except httpx.RequestError as exc:
        # Server unreachable / DNS failure / client-side timeout — no HTTP
        # response ever arrived, so map_http_error can't classify it. Surface an
        # actionable hint instead of a raw transport traceback.
        raise map_connection_error(exc, base_url) from exc


async def download(
    path: str,
    *,
    profile_name: Optional[str] = None,
    params: Optional[dict] = None,
    timeout: float = 60.0,
) -> bytes:
    """Download binary content (e.g., PDF reports)."""
    profile = get_profile(profile_name)
    base_url = profile.get("url", "http://localhost:8000").rstrip("/")
    url = f"{base_url}{path}"
    headers = _build_headers(profile)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code >= 400:
                raise map_http_error(resp.status_code)
            return resp.content
    except httpx.RequestError as exc:
        raise map_connection_error(exc, base_url) from exc


async def _try_refresh(client: httpx.AsyncClient, base_url: str, profile: dict, profile_name: Optional[str]) -> bool:
    """Attempt to refresh the JWT access token."""
    try:
        resp = await client.post(
            f"{base_url}/api/v1/auth/refresh",
            json={"refresh_token": profile["refresh_token"]},
        )
        if resp.status_code == 200:
            data = resp.json()
            profile["access_token"] = data["access_token"]
            if data.get("refresh_token"):
                profile["refresh_token"] = data["refresh_token"]
            name = profile_name or profile.get("name", "default")
            save_profile(name, profile)
            return True
    except Exception:
        pass
    return False


async def login(base_url: str, username: str, password: str) -> dict:
    """Authenticate and return tokens."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        # The endpoint is OAuth2PasswordRequestForm — form-encoded only.
        # Posting json= returns 422, which this function then reported as
        # "check username and password", blaming the user for a protocol
        # mismatch.
        resp = await client.post(
            f"{base_url.rstrip('/')}/api/v1/auth/login",
            data={"username": username, "password": password},
        )
        if resp.status_code != 200:
            raise CLIError("Login failed — check username and password", EXIT_AUTH)
        return resp.json()
