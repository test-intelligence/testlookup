"""GitHub App: the deployment's App credentials and a project's installation (W19 INT-06).

* ``GET/PUT /api/v1/settings/github-app`` (ADMIN) — App id, slug, private key and
  webhook secret. The key and the secret go to ``secret_service`` (scope
  ``github_app``) and are never returned: the read says ``*_set`` only. The key is
  parsed before it is stored (an RSA private key PEM, or a 422).
* ``GET /api/v1/github/app/install-url?project_id=`` (QA lead with access to the
  project) — ``https://github.com/apps/<slug>/installations/new?state=…``; the
  ``state`` is HMAC-signed and binds the project, the user and a 10-minute expiry.
* ``POST /api/v1/github/app/setup-callback`` ``{installation_id, state}`` — the
  page GitHub sends the browser back to posts here. The state must verify, be
  unexpired, and name the CALLING user (and a project they may still lead), or
  it is a 400: nobody can attach their installation to another project, and a
  link forwarded to another user is refused. Stores the installation id and
  switches the project's integration to ``auth_kind = "app"``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_active_user, require_role, resolve_project_scope
from app.db.postgres import get_db
from app.models.postgres import AppSetting, User, UserRole
from app.models.schemas import (
    GitHubAppInstallUrl,
    GitHubAppSettingsRead,
    GitHubAppSettingsWrite,
    GitHubAppSetupCallback,
    GitHubAppSetupResult,
)
from app.services import github_auth
from app.services.activity.service import ActorRef, record as record_activity

logger = structlog.get_logger(__name__)

settings_router = APIRouter(prefix="/api/v1/settings", tags=["GitHub App"])
router = APIRouter(prefix="/api/v1/github/app", tags=["GitHub App"])

STATE_TTL_S = 600
INVALID_STATE = "This GitHub App installation link is invalid or expired. Start the installation again from Settings › GitHub."


def _state_key() -> bytes:
    return hashlib.sha256(f"github-app-state:{settings.JWT_SECRET_KEY}".encode()).digest()


def sign_state(project_id: uuid.UUID, user_id: uuid.UUID, now: float | None = None) -> str:
    payload = {"p": str(project_id), "u": str(user_id), "e": int((now or time.time()) + STATE_TTL_S)}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(_state_key(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def verify_state(state: str, user_id: uuid.UUID, now: float | None = None) -> uuid.UUID:
    """The project the state names; ``HTTPException(400)`` with one message for every failure."""
    try:
        raw, sig = state.rsplit(".", 1)
        expected = hmac.new(_state_key(), raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError("signature")
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if int(payload["e"]) < int(now or time.time()):
            raise ValueError("expired")
        if str(payload["u"]) != str(user_id):
            raise ValueError("user")
        return uuid.UUID(str(payload["p"]))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        logger.info("github_app_state_refused", reason=type(exc).__name__ if not str(exc) else str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_STATE) from None


async def _app_row(db: AsyncSession) -> AppSetting | None:
    return (await db.execute(select(AppSetting).where(AppSetting.key == github_auth.APP_SETTING_KEY))).scalar_one_or_none()


async def _read(db: AsyncSession) -> GitHubAppSettingsRead:
    from app.services import secret_service

    row = await _app_row(db)
    value = dict(row.value or {}) if row is not None else {}
    key_set = await secret_service.has_secret(db, github_auth.APP_SCOPE, github_auth.APP_PRIVATE_KEY)
    hook_set = await secret_service.has_secret(db, github_auth.APP_SCOPE, github_auth.APP_WEBHOOK_SECRET)
    app_id, slug = value.get("app_id"), value.get("slug")
    return GitHubAppSettingsRead(app_id=app_id, slug=slug, private_key_set=bool(key_set), webhook_secret_set=bool(hook_set),
                                 configured=bool(app_id and slug and key_set))


@settings_router.get("/github-app", response_model=GitHubAppSettingsRead)
async def get_github_app_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """The deployment's GitHub App (secrets as *_set only). ADMIN."""
    return await _read(db)


@settings_router.put("/github-app", response_model=GitHubAppSettingsRead)
async def put_github_app_settings(
    payload: GitHubAppSettingsWrite,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Store the App id / slug, and the private key / webhook secret (None keeps, "" clears). ADMIN."""
    from app.services import secret_service
    from app.services.settings_audit_service import log_settings_change

    if payload.private_key:
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives.serialization import load_pem_private_key

            key = load_pem_private_key(payload.private_key.encode(), password=None)
            if not isinstance(key, rsa.RSAPrivateKey):
                raise ValueError("not RSA")
        except Exception:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="The private key must be the GitHub App's RSA private key in PEM form") from None
    changed = ["app_id", "slug"]
    for field, name in ((payload.private_key, github_auth.APP_PRIVATE_KEY), (payload.webhook_secret, github_auth.APP_WEBHOOK_SECRET)):
        if field == "":
            await secret_service.expire_secret(db, github_auth.APP_SCOPE, name)
            changed.append(name)
        elif field is not None:
            await secret_service.store_secret(db, github_auth.APP_SCOPE, name, field, current_user.id)
            changed.append(name)
    row = await _app_row(db)
    value = {"app_id": payload.app_id, "slug": payload.slug}
    if row is None:
        db.add(AppSetting(key=github_auth.APP_SETTING_KEY, value=value, updated_by=current_user.id, is_secret_backed=True))
    else:
        row.value = value
        row.updated_by = current_user.id
        row.is_secret_backed = True
    await log_settings_change(db, github_auth.APP_SETTING_KEY, "updated", current_user, changed_fields=changed)
    await db.commit()
    github_auth.clear_cache()
    logger.info("github_app_settings_updated", by=str(current_user.id), fields=changed)
    return await _read(db)


@router.get("/install-url", response_model=GitHubAppInstallUrl)
async def github_app_install_url(
    project_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
):
    """Where to install the App for this project, with a signed, user-bound, 10-minute state. QA lead."""
    await resolve_project_scope(db, current_user, str(project_id))
    app = await _read(db)
    if not app.configured or not app.slug:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The deployment's GitHub App is not configured. An administrator sets it in Settings › GitHub App.")
    url = f"https://github.com/apps/{app.slug}/installations/new?state={sign_state(project_id, current_user.id)}"
    return GitHubAppInstallUrl(url=url, expires_in=STATE_TTL_S)


@router.post("/setup-callback", response_model=GitHubAppSetupResult)
async def github_app_setup_callback(
    payload: GitHubAppSetupCallback,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Record the installation GitHub sent back, for the project the signed state names."""
    from app.services import github_checks_service as gh
    from app.services.settings_audit_service import log_settings_change

    project_id = verify_state(payload.state, current_user.id)
    role = str(getattr(current_user.role, "value", current_user.role))
    if role not in (UserRole.ADMIN.value, UserRole.QA_LEAD.value):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="QA lead or administrator only")
    await resolve_project_scope(db, current_user, str(project_id))
    row = await gh.get_integration(db, project_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Save the project's GitHub repository on Settings › GitHub first, then install the App")
    row.installation_id = int(payload.installation_id)
    row.auth_kind = "app"
    row.updated_by_user_id = current_user.id
    await log_settings_change(db, f"github_integration:{project_id}", "updated", current_user,
                              changed_fields=["auth_kind", "installation_id"])
    await record_activity(
        db, project_id=project_id, event_type="integration.connected", actor=ActorRef.from_user(current_user),
        entity_id=row.id, entity_label="GitHub App", entity_type="integration",
        context={"provider": "GitHub App", "installation_id": int(payload.installation_id)},
    )
    await db.commit()
    logger.info("github_app_installed", project_id=str(project_id), installation_id=int(payload.installation_id),
                by=str(current_user.id))
    return GitHubAppSetupResult(project_id=project_id, installation_id=int(payload.installation_id), auth_kind="app")
