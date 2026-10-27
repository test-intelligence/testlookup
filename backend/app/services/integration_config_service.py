"""Authoritative runtime resolution for global integration credentials.

W10 (INT-01 / INT-03) makes this module the ONE owner of the precedence rule
for an integration value, and of the runtime config the Splunk / OpenShift
tools and clients read:

* ``AI_OFFLINE_MODE`` (process env) overrides everything. It is read LIVE at
  use (``IntegrationRuntimeConfig.*_unavailable_reason``), never cached.
* A per-project value beats the global one (the Splunk index, the OpenShift
  namespace).
* A value saved at ``/settings/integrations`` beats the process environment;
  the environment applies only to a key never saved. A saved ``None`` or a
  saved string that is empty after ``strip()`` counts as NEVER saved, so
  clearing a field hands the key back to the environment. A saved boolean is
  always a real value (``effective_value``).
* A secret: the ``secret_refs`` row, then a legacy inline value, then the
  environment (a cleared token is expired, so the environment applies again).

``runtime_integrations_for`` serves callers that hold no session (the agent,
the log stage, ingestion's post-commit step): the bound config, else a
30-second process cache, else one short session that is CLOSED before the
caller makes any HTTP or model call. A save invalidates the saving process's
cache at once; another process sees it within ``RUNTIME_CACHE_SECONDS``.
Tokens live in process memory only (``repr=False``), never in a log, a
response, workflow state or a checkpoint.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.integration_values import valid_splunk_index
from app.models.postgres import AppSetting, Project
from app.services.secret_service import read_secret

logger = structlog.get_logger("services.integration_config")

_SCOPE = "integrations_config"

# ── Vocabularies (tests import them) ─────────────────────────────────────────

INTEGRATION_TEXT_KEYS = ("jira_domain", "jira_email", "jira_default_project_key", "splunk_base_url", "ocp_api_url",
                         "ocp_default_namespace", "slack_default_channel", "github_repo")
INTEGRATION_SECRET_KEYS = ("jira_api_token", "splunk_api_token", "ocp_sa_token", "github_token", "slack_webhook_url",
                           "teams_webhook_url")
INTEGRATION_BOOL_KEYS = ("jira_enabled", "splunk_enabled", "ocp_enabled", "slack_enabled", "teams_enabled")
VALUE_SOURCES = ("project", "saved", "environment")
UNAVAILABLE_REASONS = ("offline_mode", "disabled", "not_configured", "target_not_allowed", "invalid_index",
                       "invalid_namespace")
RUNTIME_CACHE_SECONDS = 30

#: The process-environment setting behind each saved key.
_ENV_SETTING: Mapping[str, str] = {
    "jira_enabled": "JIRA_ENABLED",
    "jira_domain": "JIRA_DOMAIN",
    "jira_email": "JIRA_EMAIL",
    "jira_default_project_key": "JIRA_DEFAULT_PROJECT_KEY",
    "splunk_enabled": "SPLUNK_ENABLED",
    "splunk_base_url": "SPLUNK_BASE_URL",
    "ocp_enabled": "OCP_ENABLED",
    "ocp_api_url": "OCP_API_URL",
    "ocp_default_namespace": "OCP_DEFAULT_NAMESPACE",
    "slack_enabled": "SLACK_ENABLED",
    "slack_default_channel": "SLACK_DEFAULT_CHANNEL",
    "teams_enabled": "TEAMS_ENABLED",
    "github_repo": "GITHUB_REPO",
}
_SECRET_ENV_SETTING: Mapping[str, str] = {
    "jira_api_token": "JIRA_API_TOKEN",
    "splunk_api_token": "SPLUNK_API_TOKEN",
    "ocp_sa_token": "OCP_SA_TOKEN",
    "github_token": "GITHUB_TOKEN",
    "slack_webhook_url": "SLACK_WEBHOOK_URL",
    "teams_webhook_url": "TEAMS_WEBHOOK_URL",
}


def effective_value(saved: Mapping[str, Any], key: str, env_value: Any) -> tuple[Any, str]:
    """(value, source). A saved bool, a saved str that is non-empty after
    strip() (returned stripped) or any other saved non-None value -> (value,
    "saved"). Otherwise (missing, None, "" / whitespace) -> (env_value,
    "environment")."""
    if key in saved:
        value = saved[key]
        if isinstance(value, bool):
            return value, "saved"
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped, "saved"
        elif value is not None:
            return value, "saved"
    return env_value, "environment"


def _non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


async def resolve_global_integrations(
    db: AsyncSession,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the persisted global integration configuration used at runtime.

    The settings screen writes metadata to ``app_settings`` and credentials to
    ``secret_refs``.  Every health probe must resolve that same pair; otherwise
    an operator can successfully save a connector while the diagnostic keeps
    testing stale process-environment values.

    Every text / boolean key goes through ``effective_value`` (a saved ``""``
    no longer beats the environment); secrets resolve ``secret_refs`` row ->
    legacy inline value -> environment. ONE added key, ``_environment_fields``:
    the sorted names of INTEGRATION_TEXT_KEYS whose source is the environment
    and whose environment value is non-empty, plus INTEGRATION_SECRET_KEYS
    whose ``secret_refs`` row and inline value are empty and whose environment
    value is non-empty. Consumers read keys by name, so the extra key reaches
    no caller unasked.
    """
    if overrides is None:
        result = await db.execute(select(AppSetting).where(AppSetting.key == _SCOPE))
        row = result.scalar_one_or_none()
        overrides = dict(row.value) if row is not None and row.value else {}

    environment_fields: list[str] = []
    secrets: dict[str, Any] = {}
    for key, setting_name in _SECRET_ENV_SETTING.items():
        default = getattr(settings, setting_name)
        stored = await read_secret(db, _SCOPE, key)
        inline = overrides.get(key)
        secrets[key] = stored or inline or default
        if not stored and not inline and _non_empty(default):
            environment_fields.append(key)

    resolved: dict[str, Any] = {}
    for key in (
        "jira_enabled", "jira_domain", "jira_email", "jira_default_project_key",
        "splunk_enabled", "splunk_base_url",
        "ocp_enabled", "ocp_api_url", "ocp_default_namespace",
        "slack_enabled", "slack_default_channel",
        "teams_enabled",
        "github_repo",
    ):
        value, source = effective_value(overrides, key, getattr(settings, _ENV_SETTING[key]))
        resolved[key] = value
        if key in INTEGRATION_TEXT_KEYS and source == "environment" and _non_empty(value):
            environment_fields.append(key)

    return {
        **resolved,
        **secrets,
        "_environment_fields": sorted(environment_fields),
    }


async def list_vault_reference_fields(db: AsyncSession) -> list[str]:
    """W20 ADM-10: the integration secret fields stored as Vault references (shown "(set — Vault)" even while Vault
    is down). Only the settings page asks; the runtime resolver above stays one read."""
    from app.models.postgres import SecretRef

    try:
        rows = await db.execute(select(SecretRef.key_name).where(
            SecretRef.scope == _SCOPE, SecretRef.provider == "vault", SecretRef.rotation_status != "expired"))
        return sorted(set(rows.scalars().all()) & set(_SECRET_ENV_SETTING))
    except Exception:  # noqa: BLE001 - a display label; never fail the settings read over it
        logger.warning("integrations: could not list Vault reference fields", exc_info=True)
        return []


async def resolve_global_notification_webhooks(
    db: AsyncSession,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve Slack/Teams global notification settings from one authority.

    Encrypted ``secret_refs`` values win, followed by legacy inline values and
    environment defaults. Callers that already loaded the AppSetting can pass
    ``overrides`` to avoid a duplicate query.
    """
    config = await resolve_global_integrations(db, overrides=overrides)
    return {
        "slack_enabled": config["slack_enabled"],
        "slack_webhook_url": config["slack_webhook_url"],
        "teams_enabled": config["teams_enabled"],
        "teams_webhook_url": config["teams_webhook_url"],
    }


# ── The runtime config the Splunk / OpenShift tools and clients read ────────


def _text(value: Any) -> Optional[str]:
    """A configured text value, stripped; None when absent or empty."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


@dataclass(frozen=True, slots=True)
class IntegrationRuntimeConfig:
    """The Splunk / OpenShift settings one investigation uses (plain values).

    Built by ``resolve_runtime_integrations`` (saved settings + the project's
    fields) or ``from_settings`` (the process environment). Frozen and kept in
    process memory only: the tokens are ``repr=False``, so neither a log line
    nor an exception message that formats the config carries them.
    """

    project_id: Optional[str]
    splunk_enabled: bool
    splunk_base_url: Optional[str]
    splunk_index: str                     # project (non-empty) -> env SPLUNK_INDEX
    splunk_index_source: str              # VALUE_SOURCES
    ocp_enabled: bool
    ocp_api_url: Optional[str]
    ocp_default_namespace: Optional[str]  # project (non-empty) -> saved global -> env OCP_DEFAULT_NAMESPACE
    ocp_namespace_source: str             # VALUE_SOURCES
    splunk_api_token: Optional[str] = field(default=None, repr=False)
    ocp_sa_token: Optional[str] = field(default=None, repr=False)

    def splunk_unavailable_reason(self) -> Optional[str]:
        """offline_mode | disabled | not_configured | invalid_index | None.

        ``AI_OFFLINE_MODE`` is read live. The egress host check is separate
        (``url_safety.egress_block_reason``, async)."""
        if settings.AI_OFFLINE_MODE:
            return "offline_mode"
        if not self.splunk_enabled:
            return "disabled"
        if not _text(self.splunk_base_url):
            return "not_configured"
        if not valid_splunk_index(self.splunk_index):
            return "invalid_index"
        return None

    def ocp_unavailable_reason(self) -> Optional[str]:
        """offline_mode | disabled | not_configured | None (offline read live)."""
        if settings.AI_OFFLINE_MODE:
            return "offline_mode"
        if not self.ocp_enabled:
            return "disabled"
        if not _text(self.ocp_api_url):
            return "not_configured"
        return None

    def namespace_for(self, run_namespace: Optional[str]) -> Optional[str]:
        """The run's own namespace when non-empty after strip, else the
        resolved default (project -> saved global -> environment)."""
        own = _text(run_namespace)
        return own if own else self.ocp_default_namespace

    @classmethod
    def from_settings(cls, settings_obj: Any, project_id: Optional[str] = None) -> "IntegrationRuntimeConfig":
        """The environment-only config built from the GIVEN settings object.

        Each tool / client passes its OWN module's ``settings``, so a test that
        patches that module's settings keeps steering it. Sources are
        ``environment``."""
        return cls(
            project_id=str(project_id) if project_id else None,
            splunk_enabled=bool(getattr(settings_obj, "SPLUNK_ENABLED", False)),
            splunk_base_url=_text(getattr(settings_obj, "SPLUNK_BASE_URL", None)),
            splunk_index=_text(getattr(settings_obj, "SPLUNK_INDEX", None)) or "",
            splunk_index_source="environment",
            ocp_enabled=bool(getattr(settings_obj, "OCP_ENABLED", False)),
            ocp_api_url=_text(getattr(settings_obj, "OCP_API_URL", None)),
            ocp_default_namespace=_text(getattr(settings_obj, "OCP_DEFAULT_NAMESPACE", None)),
            ocp_namespace_source="environment",
            splunk_api_token=_text(getattr(settings_obj, "SPLUNK_API_TOKEN", None)),
            ocp_sa_token=_text(getattr(settings_obj, "OCP_SA_TOKEN", None)),
        )


def _as_uuid(project_id: uuid.UUID | str | None) -> Optional[uuid.UUID]:
    if project_id is None or project_id == "":
        return None
    if isinstance(project_id, uuid.UUID):
        return project_id
    try:
        return uuid.UUID(str(project_id))
    except (TypeError, ValueError):
        return None


async def resolve_runtime_integrations(
    db: AsyncSession, project_id: uuid.UUID | str | None,
) -> IntegrationRuntimeConfig:
    """The runtime config for ``project_id`` (None = deployment-wide only).

    Statements: ``resolve_global_integrations(db)`` (its own), then -- only
    when a project id is given -- ONE
    ``select(Project.splunk_index, Project.ocp_namespace)``. No HTTP, no cache.
    """
    cfg = await resolve_global_integrations(db)
    environment_fields = set(cfg.get("_environment_fields") or ())

    project_index: Optional[str] = None
    project_namespace: Optional[str] = None
    pid = _as_uuid(project_id)
    if pid is not None:
        row = (await db.execute(
            select(Project.splunk_index, Project.ocp_namespace).where(Project.id == pid)
        )).first()
        if row is not None:
            project_index, project_namespace = _text(row[0]), _text(row[1])

    if project_index:
        splunk_index, index_source = project_index, "project"
    else:
        splunk_index, index_source = _text(getattr(settings, "SPLUNK_INDEX", None)) or "", "environment"

    global_namespace = _text(cfg.get("ocp_default_namespace"))
    namespace: Optional[str]
    if project_namespace:
        namespace, namespace_source = project_namespace, "project"
    elif global_namespace and "ocp_default_namespace" not in environment_fields:
        namespace, namespace_source = global_namespace, "saved"
    else:
        namespace, namespace_source = global_namespace, "environment"

    return IntegrationRuntimeConfig(
        project_id=str(pid) if pid is not None else None,
        splunk_enabled=bool(cfg.get("splunk_enabled")),
        splunk_base_url=_text(cfg.get("splunk_base_url")),
        splunk_index=splunk_index,
        splunk_index_source=index_source,
        ocp_enabled=bool(cfg.get("ocp_enabled")),
        ocp_api_url=_text(cfg.get("ocp_api_url")),
        ocp_default_namespace=namespace,
        ocp_namespace_source=namespace_source,
        splunk_api_token=_text(cfg.get("splunk_api_token")),
        ocp_sa_token=_text(cfg.get("ocp_sa_token")),
    )


# ── The 30-second process cache ──────────────────────────────────────────────

#: cache key ("" = no project) -> (monotonic time stored, config)
_RUNTIME_CACHE: dict[str, tuple[float, IntegrationRuntimeConfig]] = {}
#: Bumped on every invalidation, so a resolve that started before a save does
#: not write its (stale) answer back into the cache after it.
_CACHE_GENERATION = 0


def _cache_key(project_id: uuid.UUID | str | None) -> str:
    pid = _as_uuid(project_id)
    return str(pid) if pid is not None else ""


async def runtime_integrations_for(
    project_id: uuid.UUID | str | None,
) -> Optional[IntegrationRuntimeConfig]:
    """The runtime config for a caller WITHOUT a session.

    In order: the bound config (``investigation_context.runtime_integrations()``)
    when its project id matches; the process cache entry younger than
    ``RUNTIME_CACHE_SECONDS``; else ONE short ``AsyncSessionLocal()`` session
    that resolves, is CLOSED, and caches. Any exception -> logged at debug
    (its type only) and ``None`` (callers then use ``from_settings``). Never
    holds a session across anything else.
    """
    from app.tools.investigation_context import runtime_integrations

    key = _cache_key(project_id)
    bound = runtime_integrations()
    if bound is not None and (bound.project_id or "") == key:
        return bound

    entry = _RUNTIME_CACHE.get(key)
    if entry is not None and time.monotonic() - entry[0] < RUNTIME_CACHE_SECONDS:
        return entry[1]

    generation = _CACHE_GENERATION
    try:
        from app.db.postgres import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            resolved = await resolve_runtime_integrations(db, key or None)
    except Exception as exc:
        logger.debug("runtime integration resolve failed", error_type=type(exc).__name__)
        return None
    if generation == _CACHE_GENERATION:
        _RUNTIME_CACHE[key] = (time.monotonic(), resolved)
    return resolved


def invalidate_runtime_integrations(project_id: uuid.UUID | str | None = None) -> None:
    """Forget cached runtime configs in THIS process.

    ``None`` clears every entry (a global save); a project id clears that
    project's entry. Another process sees a save within RUNTIME_CACHE_SECONDS.
    """
    global _CACHE_GENERATION
    _CACHE_GENERATION += 1
    if project_id is None:
        _RUNTIME_CACHE.clear()
        return
    _RUNTIME_CACHE.pop(_cache_key(project_id), None)


def clear_runtime_integration_cache() -> None:
    """Tests: start from an empty cache (an autouse fixture in each module that resolves)."""
    invalidate_runtime_integrations(None)
