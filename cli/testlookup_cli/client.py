"""Shared HTTP client — handles auth injection, retries, and error mapping."""
import httpx
from typing import Any, Optional

from testlookup_cli.config import get_profile, save_profile
from testlookup_cli.errors import CLIError, map_http_error, map_connection_error, EXIT_AUTH


_MAX_BODY_SNIPPET = 160


def _error_body_snippet(resp: httpx.Response) -> str:
    """A short, readable fragment of a non-JSON error body.

    Errors that never reach the application have no ``detail`` field, because
    nothing in the app produced them. A reverse proxy answers instead, in plain
    text — and that text is usually the entire diagnosis.

    Found by fault injection: scaling Redis to zero made every backend replica
    fail its ``/health/ready`` probe, Kubernetes pulled them from the Service,
    and Traefik answered ``503 no available server``. The CLI reported
    ``Server error (503).`` and dropped the only words that explained it. The
    operator is then told the server erred, but not that there was no server.

    HTML pages are skipped — dumping a proxy's error page into a terminal is
    noise, not information — and the text is collapsed and capped so a long
    body cannot swamp the message.
    """
    try:
        body = (resp.text or "").strip()
    except Exception:  # a streamed/consumed body has no .text
        return ""
    if not body or body.startswith("<"):
        return ""
    body = " ".join(body.split())
    if len(body) > _MAX_BODY_SNIPPET:
        body = body[:_MAX_BODY_SNIPPET].rstrip() + "…"
    return body


def _raise_for_status(resp: httpx.Response) -> None:
    """Map a >=400 response to a ``CLIError``, surfacing the server's ``detail``.

    Both ``request`` and ``download`` route their status-error path through this
    one helper so they cannot drift. The ``detail`` is what turns a bare
    ``Not found.`` into an actionable message — a wrong run id, a project the key
    can't reach, a report asked for before its run completed — at exactly the
    moment a self-hoster needs to know *why* the call failed. A non-JSON or
    non-object error body falls back to a trimmed snippet of the raw body
    (:func:`_error_body_snippet`) — see there for why that matters.
    """
    if resp.status_code < 400:
        return
    detail = ""
    try:
        payload = resp.json()
        if isinstance(payload, dict):
            detail = str(payload.get("detail", "") or "")
    except Exception:
        pass
    if not detail:
        detail = _error_body_snippet(resp)
    raise map_http_error(resp.status_code, str(detail))


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
    raise_for_status: bool = True,
) -> Any:
    """Make an authenticated HTTP request to the TestLookup API.

    ``raise_for_status`` defaults to ``True`` — a >=400 response maps to a
    ``CLIError`` via :func:`_raise_for_status`. Pass ``False`` for an endpoint
    whose error status carries a *body worth reading*: ``GET /health/ready``
    answers ``503`` with a per-dependency ``checks`` object naming which of
    PostgreSQL / MongoDB / Redis is down. Collapsing that to ``"Server error
    (503)."`` throws away the one thing the caller needs, so ``health`` reads the
    body instead and decides the exit code itself. A non-JSON error body (e.g. a
    reverse proxy's HTML 502) still maps to a clean ``CLIError`` rather than
    crashing on ``resp.json()``.
    """
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

            if raise_for_status:
                _raise_for_status(resp)
            elif resp.status_code >= 400:
                # Keep an expected error body (a 503 /health/ready naming the
                # down dependency) instead of raising, but a non-JSON error body
                # still becomes a clean CLIError rather than crashing below.
                try:
                    return resp.json()
                except Exception:
                    _raise_for_status(resp)
                    raise

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
            _raise_for_status(resp)
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
