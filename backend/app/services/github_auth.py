"""The ONE reader of GitHub credentials: a project's PAT, or an installation token from the GitHub App (W19 INT-06).

Every caller that talks to a project's GitHub asks ``get_github_token(db,
project_id)`` and gets a ``GitHubCredential(token, kind)`` — or ``None`` when the
project has no usable credential. Nothing else reads the ``github_integration``
PAT secret or the App private key (``tests/regression/test_github_credentials_
single_reader.py`` holds that).

* ``auth_kind = "pat"`` (the default; every pre-0234 row): the PAT stored in
  ``secret_service`` (scope ``github_integration``, key ``project:<id>:pat``).
* ``auth_kind = "app"`` with an ``installation_id``: an installation token,
  minted from the deployment's GitHub App (Settings › GitHub App: App id, slug,
  private key, webhook secret; the key and secret in ``secret_service`` scope
  ``github_app``). The App JWT is RS256 with ``iat`` 60 s in the past and a
  9-minute ``exp`` (GitHub refuses more than 10); the exchange is
  ``POST {api_base}/app/installations/{id}/access_tokens``, SSRF-guarded like
  every GitHub call, and refused while ``AI_OFFLINE_MODE`` is on. Tokens are
  cached in process per (api_base, installation) until 5 minutes before they
  expire, and never logged. An App project with no App configured, or whose
  exchange fails, gets ``None`` — never a silent fall back to a PAT the admin
  chose not to use.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)

PAT_SCOPE = "github_integration"
APP_SCOPE = "github_app"
APP_SETTING_KEY = "github_app"
APP_PRIVATE_KEY = "private_key"
APP_WEBHOOK_SECRET = "webhook_secret"
AUTH_KINDS = ("pat", "app")
#: Refresh this long before GitHub's expiry (installation tokens live 1 hour).
_REFRESH_MARGIN_S = 300

_cache: dict[tuple[str, int], tuple[str, float]] = {}
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class GitHubCredential:
    token: str = field(repr=False)
    #: ``pat`` | ``app_installation``
    kind: str


class GitHubAppUnavailable(Exception):
    """The App path cannot produce a token; the message is safe to show."""


def pat_key(project_id: uuid.UUID | str) -> str:
    return f"project:{project_id}:pat"


async def load_app_config(db: AsyncSession) -> Optional[dict[str, Any]]:
    """``{app_id, slug, private_key}`` when the deployment's App is fully configured, else None."""
    from app.models.postgres import AppSetting
    from app.services import secret_service

    row = (await db.execute(select(AppSetting).where(AppSetting.key == APP_SETTING_KEY))).scalar_one_or_none()
    value = dict(row.value or {}) if row is not None else {}
    app_id, slug = str(value.get("app_id") or "").strip(), str(value.get("slug") or "").strip()
    if not app_id or not slug:
        return None
    key = await secret_service.read_secret(db, APP_SCOPE, APP_PRIVATE_KEY)
    if not key:
        return None
    return {"app_id": app_id, "slug": slug, "private_key": key}


def app_jwt(app_id: str, private_key_pem: str, now: Optional[float] = None) -> str:
    """The App's RS256 JWT: ``iat`` 60 s back (clock drift), ``exp`` 9 minutes on, ``iss`` the App id."""
    import jwt

    t = int(now if now is not None else time.time())
    return jwt.encode({"iat": t - 60, "exp": t + 540, "iss": str(app_id)}, private_key_pem, algorithm="RS256")


def _parse_expiry(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except (TypeError, ValueError):
        return time.time() + 3600


async def installation_token(api_base: str, installation_id: int, app: dict[str, Any]) -> str:
    """A cached or freshly minted installation token. Raises ``GitHubAppUnavailable``."""
    import httpx

    from app.core.http_client import http_verify
    from app.services.url_safety import egress_block_reason

    base = api_base.rstrip("/")
    cache_key = (base, int(installation_id))
    with _cache_lock:
        hit = _cache.get(cache_key)
    if hit and hit[1] - _REFRESH_MARGIN_S > time.time():
        return hit[0]
    if settings.AI_OFFLINE_MODE:
        raise GitHubAppUnavailable("AI_OFFLINE_MODE is enabled — outbound calls are disabled")
    url = f"{base}/app/installations/{int(installation_id)}/access_tokens"
    block = await egress_block_reason(url)
    if block:
        raise GitHubAppUnavailable(f"Target host is not allowed ({block})")
    headers = {
        "Authorization": f"Bearer {app_jwt(app['app_id'], app['private_key'])}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "TestLookup/1.0",
    }
    try:
        async with httpx.AsyncClient(verify=http_verify(), timeout=10.0, follow_redirects=False) as client:
            resp = await client.post(url, headers=headers)
    except Exception as exc:  # noqa: BLE001
        raise GitHubAppUnavailable(f"Network error minting an installation token: {type(exc).__name__}") from exc
    if resp.status_code != 201:
        message = ""
        try:
            message = str((resp.json() or {}).get("message") or "")
        except Exception:  # noqa: BLE001
            pass
        raise GitHubAppUnavailable(f"GitHub refused the installation token ({resp.status_code}{': ' + message if message else ''})")
    body = resp.json() or {}
    token = str(body.get("token") or "")
    if not token:
        raise GitHubAppUnavailable("GitHub answered without a token")
    with _cache_lock:
        _cache[cache_key] = (token, _parse_expiry(body.get("expires_at")))
    return token


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


async def get_github_token(
    db: AsyncSession,
    project_id: uuid.UUID | str,
    *,
    integration: Any = None,
    raise_app_errors: bool = False,
) -> Optional[GitHubCredential]:
    """The project's GitHub credential, or None. ``integration`` skips a re-read when the caller has the row."""
    from app.services import secret_service

    row = integration
    if row is None:
        from app.models.postgres import GitHubIntegration

        row = (await db.execute(
            select(GitHubIntegration).where(GitHubIntegration.project_id == uuid.UUID(str(project_id)))
        )).scalar_one_or_none()
    if row is None:
        return None
    if getattr(row, "auth_kind", "pat") == "app":
        if not row.installation_id:
            if raise_app_errors:
                raise GitHubAppUnavailable("GitHub App selected but not installed for this project yet")
            return None
        app = await load_app_config(db)
        if app is None:
            if raise_app_errors:
                raise GitHubAppUnavailable("The deployment's GitHub App is not configured (Settings › GitHub App)")
            return None
        try:
            token = await installation_token(row.api_base_url, int(row.installation_id), app)
        except GitHubAppUnavailable as exc:
            logger.warning("github_app_token_unavailable: project=%s reason=%s", project_id, exc)
            if raise_app_errors:
                raise
            return None
        return GitHubCredential(token=token, kind="app_installation")
    pat = await secret_service.read_secret(db, PAT_SCOPE, pat_key(project_id))
    return GitHubCredential(token=pat, kind="pat") if pat else None
