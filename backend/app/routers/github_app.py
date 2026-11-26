"""GitHub App: the deployment's App credentials and a project's installation (W19 INT-06).

* ``GET/PUT /api/v1/settings/github-app`` (ADMIN) — App id, slug, private key and
  webhook secret. The key and the secret go to ``secret_service`` (scope
  ``github_app``) and are never returned: the read says ``*_set`` only. The key is
  parsed before it is stored (an RSA private key PEM, or a 422).
* ``GET /api/v1/github/app/install-url?project_id=`` (ADMIN) — creates an opaque,
  server-stored, 10-minute transaction bound to the administrator, project,
  deployment App, API base, and configured repository.
* ``POST /api/v1/github/app/setup-callback`` ``{installation_id, state}`` — the
  page GitHub sends the browser back to posts here. The transaction is burned
  before validation. Only its initiating administrator may complete it, and the
  deployment App must mint a token that can read the transaction's repository
  before the installation id is stored.
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_active_user, require_role, resolve_project_scope
from app.db.postgres import get_db, release_read_connection
from app.models.postgres import AppSetting, User, UserRole
from app.models.schemas import (
    GitHubAppInstallUrl,
    GitHubAppSettingsRead,
    GitHubAppSettingsWrite,
    GitHubAppSetupCallback,
    GitHubAppSetupResult,
)
from app.services import github_app_setup_state, github_auth
from app.services.activity.service import ActorRef, record as record_activity
from app.services.url_safety import same_api_base

logger = structlog.get_logger(__name__)

settings_router = APIRouter(prefix="/api/v1/settings", tags=["GitHub App"])
router = APIRouter(prefix="/api/v1/github/app", tags=["GitHub App"])

INVALID_STATE = "This GitHub App installation link is invalid or expired. Start the installation again from Settings › GitHub."
SETUP_CHANGED = "GitHub App setup changed while it was in progress. Start the installation again from Settings › GitHub."
SETUP_UNAVAILABLE = "GitHub App setup is temporarily unavailable. Try again from Settings › GitHub."


def _setup_matches(transaction: github_app_setup_state.SetupTransaction, row, app, app_base: str) -> bool:
    return (
        app is not None
        and str(app.get("app_id")) == transaction.app_id
        and same_api_base(app_base, transaction.api_base_url)
        and same_api_base(row.api_base_url, transaction.api_base_url)
        and row.repo_owner == transaction.repo_owner
        and row.repo_name == transaction.repo_name
    )


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
            await secret_service.store_secret(db, github_auth.APP_SCOPE, name, field, current_user.id, allow_reference=True)
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
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Create an administrator-approved setup transaction for this project's configured repository."""
    from app.services import github_checks_service as gh

    await resolve_project_scope(db, current_user, str(project_id))
    app = await _read(db)
    if not app.configured or not app.slug or not app.app_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The deployment's GitHub App is not configured. An administrator sets it in Settings › GitHub App.")
    row = await gh.get_integration(db, project_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Save the project's GitHub repository on Settings › GitHub first, then install the App")
    app_base = (settings.GITHUB_APP_API_BASE_URL or "").strip().rstrip("/")
    if not same_api_base(row.api_base_url, app_base):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The project's GitHub API base must match the deployment GitHub App before installation.",
        )
    transaction = github_app_setup_state.SetupTransaction(
        project_id=project_id,
        user_id=current_user.id,
        app_id=str(app.app_id),
        api_base_url=app_base,
        repo_owner=row.repo_owner,
        repo_name=row.repo_name,
    )
    try:
        state_value = await github_app_setup_state.issue_setup_transaction(transaction)
    except github_app_setup_state.SetupStateUnavailable:
        logger.warning("github_app_setup_state_unavailable", phase="issue")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=SETUP_UNAVAILABLE) from None
    url = f"https://github.com/apps/{app.slug}/installations/new?state={state_value}"
    return GitHubAppInstallUrl(url=url, expires_in=github_app_setup_state.STATE_TTL_SECONDS)


@router.post("/setup-callback", response_model=GitHubAppSetupResult)
async def github_app_setup_callback(
    payload: GitHubAppSetupCallback,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Verify and record the installation selected by the initiating administrator."""
    from app.services import github_checks_service as gh
    from app.services.settings_audit_service import log_settings_change

    try:
        transaction = await github_app_setup_state.consume_setup_transaction(payload.state)
    except github_app_setup_state.SetupStateUnavailable:
        logger.warning("github_app_setup_state_unavailable", phase="consume")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=SETUP_UNAVAILABLE) from None
    if transaction is None or transaction.user_id != current_user.id:
        logger.info("github_app_setup_state_refused", reason="unknown_or_actor_mismatch")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_STATE)
    role = str(getattr(current_user.role, "value", current_user.role))
    if role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="An instance administrator must approve GitHub App installations")
    project_id = transaction.project_id
    await resolve_project_scope(db, current_user, str(project_id))
    row = await gh.get_integration(db, project_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Save the project's GitHub repository on Settings › GitHub first, then install the App")
    app_base = (settings.GITHUB_APP_API_BASE_URL or "").strip().rstrip("/")
    app = await github_auth.load_app_config(db)
    if not _setup_matches(transaction, row, app, app_base):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=SETUP_CHANGED)
    assert app is not None  # narrowed by _setup_matches; keeps the verifier's credential input explicit
    # Do not hold a pooled Postgres connection across GitHub's network calls.
    await release_read_connection(db)
    try:
        await github_auth.verify_installation_repository(
            app_base,
            int(payload.installation_id),
            app,
            transaction.repo_owner,
            transaction.repo_name,
        )
    except github_auth.GitHubAppUnavailable:
        logger.info("github_app_installation_refused", project_id=str(project_id), reason="github_verification_failed")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="GitHub did not confirm that this App installation can access the configured repository. "
                   "Check the repository selection and start the installation again.",
        ) from None
    # Re-read under a row lock after the network boundary. Every integration
    # update takes the same lock, so a concurrent repository change either
    # fails this comparison or runs later and clears this installation id.
    row = await gh.get_integration(db, project_id, for_update=True)
    if row is None or not _setup_matches(transaction, row, app, app_base):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=SETUP_CHANGED)
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
