"""
Async HTTP client for the TestLookup backend.

Handles JWT authentication automatically:
- Authenticates on first request (if credentials are configured)
- Re-authenticates transparently on 401
- Falls back to unauthenticated requests if no credentials are set
"""

from __future__ import annotations

import ipaddress
import logging
import re
import uuid
from typing import Any, Optional
from urllib.parse import quote

import httpx

from config import settings  # type: ignore[import]

logger = logging.getLogger("testlookup.mcp.client")

_stdio_access_token: Optional[str] = None
_client: Optional[httpx.AsyncClient] = None
_auth_client: Optional[httpx.AsyncClient] = None
_plain_http_warned = False


# ── Backend paths ─────────────────────────────────────────────────────────────
#
# Tool arguments are free strings chosen by the calling model. Put into a URL
# path as they are, an id such as ``../reviews/<id>/accept?`` sends the request
# to a different backend route than the tool is meant to call. So the request
# helpers below only take a path built by ``path()``, which percent-encodes
# every value, and they check the finished URL once more before sending.

_STATIC_SEGMENT = re.compile(r"^[A-Za-z0-9_\-.]+$")
_PLACEHOLDER = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")


try:  # The SDK shows a ToolError / ResourceError message to the caller and hides any other exception's text.
    from mcp.server.mcpserver.exceptions import ResourceError as _ResourceError
    from mcp.server.mcpserver.exceptions import ToolError as _ToolError
except ImportError:  # pragma: no cover - the SDK is always installed where the server runs

    class _ToolError(Exception):  # type: ignore[no-redef]
        pass

    class _ResourceError(Exception):  # type: ignore[no-redef]
        pass


class PathArgumentError(_ToolError, _ResourceError, ValueError):
    """A tool or resource argument cannot be used as part of a backend path.

    The message is written for the caller: it says which argument was refused
    and what to pass instead.
    """


class ApiPath(str):
    """A backend path built by ``path()``; the only kind the request helpers send.

    ``prefix`` is the fixed text of the template before its first value.
    """

    prefix: str

    def __new__(cls, value: str, prefix: str) -> "ApiPath":
        obj = super().__new__(cls, value)
        obj.prefix = prefix
        return obj


class _UuidSegment:
    __slots__ = ("value",)

    def __init__(self, value: object) -> None:
        self.value = value


def uuid_id(value: object) -> _UuidSegment:
    """Mark a ``path()`` value that the backend route declares as a UUID."""
    return _UuidSegment(value)


def _shown(value: object) -> str:
    text = repr(str(value))
    return text if len(text) <= 60 else text[:57] + "...'"


def _encode_segment(name: str, value: object) -> str:
    if isinstance(value, _UuidSegment):
        try:
            return str(uuid.UUID(str(value.value).strip()))
        except (ValueError, AttributeError, TypeError):
            raise PathArgumentError(
                f"{name} must be a UUID such as 123e4567-e89b-12d3-a456-426614174000, "
                f"but got {_shown(value.value)}. Pass the id exactly as a list or search tool returned it."
            ) from None
    text = "" if value is None else str(value)
    if text.strip() in ("", ".", ".."):
        raise PathArgumentError(
            f"{name} must not be empty, '.' or '..' (got {_shown(text)}). "
            "Pass the id or key exactly as a list tool returned it."
        )
    return quote(text, safe="")


def path(template: str, /, **values: object) -> ApiPath:
    """Build a backend path from a fixed template and caller-supplied values.

    ``path("/api/v1/runs/{run_id}/tests", run_id=uuid_id(run_id))``

    The template is a literal: fixed segments plus ``{name}`` placeholders, each
    a whole segment. Every value is percent-encoded with ``quote(value,
    safe="")``, so ``/``, ``?``, ``#`` and ``%`` in it stay inside its own
    segment; an empty value, ``.`` and ``..`` are refused. Wrap a value in
    ``uuid_id()`` when the backend route takes a UUID: anything else is refused
    here with a message the caller can act on, before any request is sent.
    """
    if not isinstance(template, str) or not template.startswith("/") or template.startswith("//"):
        raise ValueError(f"path template must start with a single '/': {template!r}")
    used: list[str] = []
    built: list[str] = []
    prefix_parts: list[str] = []
    for part in template[1:].split("/"):
        placeholder = _PLACEHOLDER.match(part)
        if placeholder:
            name = placeholder.group(1)
            if name not in values:
                raise ValueError(f"path template {template!r} needs a value for {name!r}")
            if name in used:
                raise ValueError(f"path template {template!r} repeats {name!r}")
            used.append(name)
            built.append(_encode_segment(name, values[name]))
        elif _STATIC_SEGMENT.match(part) and part not in (".", ".."):
            built.append(part)
            if not used:
                prefix_parts.append(part)
        else:
            raise ValueError(
                f"path template {template!r} has a segment that is neither fixed text nor a "
                f"{{name}} placeholder: {part!r}. Pass values as keyword arguments, never inside the template."
            )
    extra = sorted(set(values) - set(used))
    if extra:
        raise ValueError(f"path template {template!r} has no placeholder for {extra}")
    prefix = "/" + "/".join(prefix_parts) + ("/" if used else "")
    return ApiPath("/" + "/".join(built), prefix)


def _checked_path(client: Any, target: object) -> ApiPath:
    """Refuse anything but a ``path()`` result whose final URL stays under its fixed prefix."""
    if not isinstance(target, ApiPath):
        raise TypeError(
            "The backend client only sends paths built with client.path(template, **values). "
            f"Got a plain {type(target).__name__}: {str(target)[:80]!r}."
        )
    text = str(target)
    inside = (
        text.startswith(target.prefix)
        and not text.startswith("//")
        and not any(ch in text for ch in "?#\\")
        and not any(segment in ("", ".", "..") for segment in text[1:].split("/"))
    )
    if inside and isinstance(client, httpx.AsyncClient):
        # What httpx will really send: the base URL joined with the path.
        final = client.build_request("GET", text).url
        base = client.base_url
        base_path = base.raw_path.split(b"?", 1)[0].rstrip(b"/")
        inside = (
            (final.scheme, final.host, final.port) == (base.scheme, base.host, base.port)
            and final.raw_path == base_path + text.encode("ascii")
            and final.raw_path.startswith(base_path + target.prefix.encode("ascii"))
        )
    if not inside:
        raise PathArgumentError(
            f"Refused to send the request: its path would leave {target.prefix!r}. "
            "Pass ids exactly as a list or search tool returned them."
        )
    return target


def _is_loopback(host: str) -> bool:
    name = (host or "").strip("[]").lower()
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def warn_if_credentials_leave_in_plain_http() -> None:
    """Log once per process when a login would send the password unencrypted off this machine."""
    global _plain_http_warned
    if _plain_http_warned:
        return
    try:
        url = httpx.URL(settings.api_url)
    except Exception:  # noqa: BLE001 - a malformed URL fails later, with httpx's own message
        return
    if url.scheme == "http" and not _is_loopback(url.host):
        _plain_http_warned = True
        logger.warning(
            "TESTLOOKUP_API_URL is plain http to %s, which is not this machine: the TestLookup "
            "username and password are sent unencrypted and anyone on the network path can read them. "
            "Use an https:// URL. (This warning is logged once per process.)",
            url.host,
        )


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
    warn_if_credentials_leave_in_plain_http()
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


async def public_health() -> dict[str, Any]:
    """Version and readiness from the two public probes, with no credential.

    ``/health/ready`` answers 503 with the same body when a data store is
    down, so that status is read, not raised.
    """
    client = _get_client()
    merged: dict[str, Any] = {}
    for probe in (path("/health/version"), path("/health/ready")):
        resp = await client.get(_checked_path(client, probe))
        if resp.status_code not in (200, 503):
            resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict):
            merged.update(body)
    return merged


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


async def get(path: ApiPath, params: Optional[dict] = None) -> Any:
    client = _get_client()
    path = _checked_path(client, path)
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
    path: ApiPath,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
    extra_headers: Optional[dict] = None,
) -> Any:
    """POST with the caller's auth. ``extra_headers`` (e.g. Idempotency-Key)
    are sent too, but can never override the auth headers."""
    client = _get_client()
    path = _checked_path(client, path)
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


async def patch(path: ApiPath, json_body: Optional[dict] = None) -> Any:
    """PATCH helper — identical 401-retry semantics as ``post``."""
    client = _get_client()
    path = _checked_path(client, path)
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


async def put(path: ApiPath, json_body: Optional[dict] = None) -> Any:
    """PUT helper — used by config upserts (feature flags, quotas)."""
    client = _get_client()
    path = _checked_path(client, path)
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


async def delete(path: ApiPath) -> None:
    """DELETE helper — most endpoints return 204 No Content."""
    client = _get_client()
    path = _checked_path(client, path)
    headers, caller_scoped = await _auth_context()
    resp = await client.delete(path, headers=headers)

    if resp.status_code == 401 and not caller_scoped and settings.username:
        global _stdio_access_token
        _stdio_access_token = None
        headers, _ = await _auth_context()
        resp = await client.delete(path, headers=headers)

    resp.raise_for_status()
    return None
