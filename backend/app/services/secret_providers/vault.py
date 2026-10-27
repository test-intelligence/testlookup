"""W20 ADM-10: read secret REFERENCES from a Vault-compatible KV v2 store (HashiCorp Vault, OpenBao).

An administrator may enter ``vault:<path>#<key>`` in any secret field instead of a value; ``secret_service``
stores the pointer (``provider='vault'``) and resolves it here at read time. Plain HTTP via httpx (no ``hvac``):

* the address comes from ``SECRET_PROVIDER_VAULT_ADDR`` only (deployment config, never the UI), so the reference
  cannot steer a request to another host; the path is restricted to ``[A-Za-z0-9_.-/]`` without ``..``;
* auth ``token`` | ``approle`` | ``kubernetes``; the client token is cached until shortly before its lease ends and
  re-acquired once on a 403;
* values are cached for ``SECRET_PROVIDER_VAULT_CACHE_SECONDS`` (a changed Vault value takes effect within it);
* any failure answers ``None`` (the caller treats it as "no secret") and counts ``secret_read_failures_total``;
  a resolved value is never logged.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
import structlog

from app.core.config import settings
from app.core.http_client import http_verify

logger = structlog.get_logger("services.secret_providers.vault")

PREFIX = "vault:"
_PATH = re.compile(r"^[A-Za-z0-9_.\-/]{1,400}$")
_KEY = re.compile(r"^[A-Za-z0-9_.\-]{1,200}$")
_K8S_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_TIMEOUT = 10.0


class VaultReferenceError(ValueError):
    """The text looks like a reference but is not a valid ``vault:<path>#<key>``."""


@dataclass(frozen=True)
class VaultRef:
    path: str
    key: str

    def __str__(self) -> str:
        return f"{PREFIX}{self.path}#{self.key}"


def is_reference(value: Optional[str]) -> bool:
    return isinstance(value, str) and value.strip().startswith(PREFIX)


def parse_reference(value: str) -> VaultRef:
    """``vault:<path>#<key>`` → ``VaultRef``; raises ``VaultReferenceError`` with the reason."""
    body = value.strip()[len(PREFIX):]
    path, sep, key = body.partition("#")
    path = path.strip().strip("/")
    key = key.strip()
    if not sep or not path or not key:
        raise VaultReferenceError("A Vault reference is vault:<path>#<key>, e.g. vault:testlookup/jira#api_token")
    if ".." in path.split("/") or not _PATH.match(path) or not _KEY.match(key):
        raise VaultReferenceError("A Vault reference path may hold letters, digits and _ . - / only (no '..')")
    return VaultRef(path=path, key=key)


def configured() -> bool:
    addr = (settings.SECRET_PROVIDER_VAULT_ADDR or "").strip()
    try:
        parts = urlsplit(addr)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def _addr() -> str:
    return settings.SECRET_PROVIDER_VAULT_ADDR.strip().rstrip("/")


def _headers(token: Optional[str] = None) -> dict[str, str]:
    h = {"Accept": "application/json"}
    if token:
        h["X-Vault-Token"] = token
    ns = (settings.SECRET_PROVIDER_VAULT_NAMESPACE or "").strip()
    if ns:
        h["X-Vault-Namespace"] = ns
    return h


class _State:
    token: Optional[str] = None
    token_expires: float = 0.0
    values: dict[tuple[str, str], tuple[float, str]] = {}


def clear_cache() -> None:
    _State.token, _State.token_expires, _State.values = None, 0.0, {}


async def _login(client: httpx.AsyncClient) -> Optional[str]:
    method = (settings.SECRET_PROVIDER_VAULT_AUTH or "token").strip().lower()
    if method == "token":
        return settings.SECRET_PROVIDER_VAULT_TOKEN or None
    if method == "approle":
        body: dict[str, Any] = {"role_id": settings.SECRET_PROVIDER_VAULT_ROLE_ID,
                                "secret_id": settings.SECRET_PROVIDER_VAULT_SECRET_ID}
        url = f"{_addr()}/v1/auth/approle/login"
    elif method == "kubernetes":
        try:
            with open(_K8S_TOKEN, encoding="utf-8") as fh:
                jwt = fh.read().strip()
        except OSError:
            logger.warning("vault kubernetes login: no service-account token in the pod")
            return None
        body = {"role": settings.SECRET_PROVIDER_VAULT_K8S_ROLE, "jwt": jwt}
        url = f"{_addr()}/v1/auth/kubernetes/login"
    else:
        logger.warning("vault login: unknown SECRET_PROVIDER_VAULT_AUTH", method=method)
        return None
    resp = await client.post(url, json=body, headers=_headers())
    if resp.status_code != 200:
        logger.warning("vault login refused", method=method, status_code=resp.status_code)
        return None
    auth = (resp.json() or {}).get("auth") or {}
    token = auth.get("client_token")
    lease = int(auth.get("lease_duration") or 0)
    _State.token_expires = time.monotonic() + max(lease - 30, 30) if lease else time.monotonic() + 300
    return token or None


async def _token(client: httpx.AsyncClient, *, fresh: bool = False) -> Optional[str]:
    renewable = (settings.SECRET_PROVIDER_VAULT_AUTH or "token").strip().lower() != "token"
    if fresh or not _State.token or (renewable and time.monotonic() >= _State.token_expires):
        _State.token = await _login(client)
    return _State.token


def _count_failure() -> None:
    try:
        from app.core.metrics import secret_read_failures_total

        secret_read_failures_total.labels(scope="vault").inc()
    except Exception:  # noqa: BLE001 -- telemetry must never break a read
        pass


async def _get(client: httpx.AsyncClient, ref: VaultRef) -> tuple[Optional[str], str]:
    mount = (settings.SECRET_PROVIDER_VAULT_MOUNT or "secret").strip("/")
    url = f"{_addr()}/v1/{mount}/data/{ref.path}"
    for attempt in (0, 1):
        token = await _token(client, fresh=attempt == 1)
        if not token:
            return None, "Vault login failed"
        resp = await client.get(url, headers=_headers(token))
        if resp.status_code == 403 and attempt == 0:
            continue
        if resp.status_code == 404:
            return None, "the path does not exist in Vault"
        if resp.status_code != 200:
            return None, f"Vault answered {resp.status_code}"
        data = ((resp.json() or {}).get("data") or {}).get("data") or {}
        value = data.get(ref.key)
        if value is None:
            return None, "the key does not exist at that path"
        return str(value), "ok"
    return None, "Vault refused the token"


async def read(reference: str) -> Optional[str]:
    """The value a reference points at, or ``None`` (not configured, refused, missing, unreachable)."""
    try:
        ref = parse_reference(reference)
    except VaultReferenceError:
        _count_failure()
        return None
    if not configured():
        logger.warning("vault reference read while SECRET_PROVIDER_VAULT_ADDR is unset")
        _count_failure()
        return None
    cached = _State.values.get((ref.path, ref.key))
    if cached and cached[0] > time.monotonic():
        return cached[1]
    try:
        async with httpx.AsyncClient(verify=http_verify(), timeout=_TIMEOUT) as client:
            value, why = await _get(client, ref)
    except httpx.HTTPError as exc:
        logger.warning("vault read failed", error=type(exc).__name__)
        _count_failure()
        return None
    if value is None:
        logger.warning("vault reference did not resolve", reason=why)
        _count_failure()
        return None
    _State.values[(ref.path, ref.key)] = (time.monotonic() + max(settings.SECRET_PROVIDER_VAULT_CACHE_SECONDS, 0), value)
    return value


async def health() -> dict[str, Any]:
    """Provider status for the admin card: configured, the host (never a token), the auth method, login ok."""
    out: dict[str, Any] = {
        "name": "vault",
        "configured": configured(),
        "address_host": urlsplit(_addr()).hostname if configured() else None,
        "auth_method": (settings.SECRET_PROVIDER_VAULT_AUTH or "token").strip().lower(),
        "healthy": False,
        "message": "Not configured: set SECRET_PROVIDER_VAULT_ADDR on the deployment.",
    }
    if not out["configured"]:
        return out
    try:
        async with httpx.AsyncClient(verify=http_verify(), timeout=_TIMEOUT) as client:
            token = await _token(client, fresh=True)
            if not token:
                out["message"] = "Vault login failed with the configured credentials."
                return out
            resp = await client.get(f"{_addr()}/v1/auth/token/lookup-self", headers=_headers(token))
    except httpx.HTTPError as exc:
        out["message"] = f"Vault is unreachable ({type(exc).__name__})."
        return out
    if resp.status_code != 200:
        out["message"] = f"Vault answered {resp.status_code} to the token check."
        return out
    out.update(healthy=True, message="Healthy: logged in and the token is valid.")
    return out
