"""
Integration Probe Service — active health probes for external systems (OPS-01).

Probes: Jira, Splunk, GitHub, OCP/K8s, Slack, Teams, SMTP, Ollama, ChromaDB.
Records auth validity, latency, payload correctness.
Alerts on sustained failures (consecutive_failures >= threshold).

Per-project probes (W10 INT-04). GitHub / GitHub Enterprise and GitLab are
configured per project (Settings > GitHub / GitLab), so each enabled
integration is probed against ITS OWN base with ITS OWN token and stored under
``(provider, scope_key = str(project_id))``; a deployment-wide row keeps
``scope_key = ''``. ``run_project_probes`` reads every target on ONE session,
hands that session's connection back (or closes it), and only then probes over
HTTP with at most ``PROJECT_PROBE_CONCURRENCY`` requests in flight -- no
session is ever held across a call. ``persist_probe_results`` owns the one
commit (the transaction allow-list holds this module at 1).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from app.core.http_client import get_http_client
from app.services.url_safety import egress_block_reason

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.github_target import GitHubTargetResolution
    from app.services.gitlab_integration_service import GitLabTargetResolution

logger = logging.getLogger("services.integration_probe")

ALERT_THRESHOLD = 3  # consecutive failures before alerting
INTEGRATION_HEALTH_VALUES = {
    "healthy": 1.0,
    "degraded": 0.5,
    "down": 0.0,
    "auth_error": 0.0,
    "timeout": 0.0,
    "skipped": -1.0,
}

# ── Per-project probes (W10 INT-04): the vocabularies, in ONE place ──────────

#: Providers configured per project, and therefore probed per project.
PROJECT_PROBE_PROVIDERS = ("github", "gitlab")
#: At most this many per-project probe requests are in flight at once.
PROJECT_PROBE_CONCURRENCY = 5
#: ``IntegrationHealthRow.kind``: a deployment-wide row, a project's
#: integration, or a paused outbound webhook subscription.
HEALTH_ROW_KINDS = ("global", "project", "webhook")
#: ``GET /integration-health/status?scope=``.
HEALTH_SCOPES = ("global", "all")
#: What a project row says before its first probe. Absence is not health:
#: such a row reads ``probed: false`` / ``status: "unknown"``, never healthy.
NOT_PROBED_MESSAGE = "Not probed yet. Probe now, or wait for the 15-minute check."

_PROVIDER_LABEL = {"github": "GitHub", "gitlab": "GitLab"}
_PROBE_TIMEOUT = 10.0


@dataclass
class ProbeResult:
    provider: str
    status: str  # healthy | degraded | down | auth_error | timeout
    response_ms: int = 0
    message: str = ""
    auth_valid: bool | None = None
    payload_valid: bool | None = None
    # W10 INT-04: "" = deployment-wide; else the project id of a per-project
    # integration. ``target`` is what a page may show of the probed base
    # (github_target.target_label: host + path, never a credential).
    scope_key: str = ""
    project_id: str | None = None
    target: str | None = None


async def _offline_refusal(provider: str, destination: str | None) -> ProbeResult | None:
    """``skipped`` when offline mode forbids a notification probe's destination.

    Re-audit H10 (QA): the 15-minute health task probed the SMTP relay --
    logging in to it -- and Slack's API with the bot token, on deployments
    whose offline ceiling refused every notification to the same places. The
    notification channels are judged by residency, as their senders are.
    """
    from app.services.notification.egress import (
        OfflineEgressBlocked,
        assert_delivery_allowed_async,
    )

    try:
        # A probe checks the deployment's own configuration only (the SMTP
        # relay, Slack's API with the bot token), so the allow-list applies.
        await assert_delivery_allowed_async(provider, destination, deployment_wide=True)
    except OfflineEgressBlocked as exc:
        return ProbeResult(provider.lower(), "skipped", message=str(exc)[:300])
    return None


def _offline_hard_gate(provider: str) -> ProbeResult | None:
    """``skipped`` in offline mode for an integration whose calls offline mode forbids.

    Jira and GitHub refuse every outbound call when ``AI_OFFLINE_MODE`` is on
    (``defect_jira_service``, ``github_checks_service``). A probe carries the
    same credentials, so it must not make the call either.

    Splunk and OpenShift are skipped too (code review of H10): each probe sent
    its bearer token to the configured API every 15 minutes. Their
    integrations are not all gated yet (re-audit N19), but a probe must not
    add egress of its own while that is open.
    """
    from app.core.config import settings

    if settings.AI_OFFLINE_MODE:
        return ProbeResult(
            provider, "skipped", message=f"AI_OFFLINE_MODE=true -- outbound {provider} calls are disabled"
        )
    return None


async def probe_jira(config: dict | None = None) -> ProbeResult:
    """Probe Jira REST API: check auth and server info."""
    from app.core.config import settings

    enabled = config.get("jira_enabled") if config is not None else settings.JIRA_ENABLED
    domain = config.get("jira_domain") if config is not None else settings.JIRA_DOMAIN
    email = config.get("jira_email") if config is not None else settings.JIRA_EMAIL
    token = config.get("jira_api_token") if config is not None else settings.JIRA_API_TOKEN
    if not enabled or not domain:
        return ProbeResult("jira", "skipped", message="JIRA_ENABLED=false or no domain configured")
    refused = _offline_hard_gate("jira")
    if refused:
        return refused

    import httpx

    start = time.monotonic()
    try:
        url = f"https://{domain}/rest/api/3/serverInfo"
        client = get_http_client()
        resp = await client.get(
            url,
            auth=(email or "", token or ""),
            timeout=10.0,
        )
        ms = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            return ProbeResult("jira", "healthy", ms, f"Server: {data.get('serverTitle', 'OK')}", True, True)
        if resp.status_code in (401, 403):
            return ProbeResult("jira", "auth_error", ms, f"HTTP {resp.status_code}: Authentication failed", False)
        return ProbeResult("jira", "degraded", ms, f"HTTP {resp.status_code}")
    except httpx.TimeoutException:
        return ProbeResult("jira", "timeout", int((time.monotonic() - start) * 1000), "Connection timed out")
    except Exception as exc:
        return ProbeResult("jira", "down", 0, str(exc)[:300])


async def probe_splunk(config: dict | None = None) -> ProbeResult:
    """Probe Splunk REST API: check auth and service availability."""
    from app.core.config import settings

    enabled = config.get("splunk_enabled") if config is not None else settings.SPLUNK_ENABLED
    base_url = config.get("splunk_base_url") if config is not None else settings.SPLUNK_BASE_URL
    token = config.get("splunk_api_token") if config is not None else settings.SPLUNK_API_TOKEN
    if not enabled or not base_url:
        return ProbeResult("splunk", "skipped", message="SPLUNK_ENABLED=false or no URL configured")
    refused = _offline_hard_gate("splunk")
    if refused:
        return refused
    # W10 (D-5): the operator-configured base is checked before the token is
    # sent: a loopback / link-local / unspecified host is refused, no request.
    block = await egress_block_reason(base_url)
    if block:
        return ProbeResult("splunk", "down", 0, f"Target host is not allowed ({block})")

    import httpx

    start = time.monotonic()
    try:
        url = f"{base_url}/services/server/info"
        headers = {"Authorization": f"Bearer {token or ''}"}
        client = get_http_client()
        resp = await client.get(url, headers=headers, timeout=10.0)
        ms = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            return ProbeResult("splunk", "healthy", ms, "Splunk API reachable", True, True)
        if resp.status_code in (401, 403):
            return ProbeResult("splunk", "auth_error", ms, f"HTTP {resp.status_code}", False)
        return ProbeResult("splunk", "degraded", ms, f"HTTP {resp.status_code}")
    except httpx.TimeoutException:
        return ProbeResult("splunk", "timeout", int((time.monotonic() - start) * 1000), "Timed out")
    except Exception as exc:
        return ProbeResult("splunk", "down", 0, str(exc)[:300])


async def probe_github(config: dict | None = None) -> ProbeResult:
    """Probe GitHub API: check token validity."""
    from app.core.config import settings

    token = config.get("github_token") if config is not None else settings.GITHUB_TOKEN
    if not token:
        # GitHub is configured per project (Settings > GitHub); this probes only an
        # instance-wide token, so say that, not that GitHub is unconfigured.
        return ProbeResult(
            "github",
            "skipped",
            message="No instance-wide GITHUB_TOKEN; GitHub is configured per project (Settings > GitHub)",
        )
    refused = _offline_hard_gate("github")
    if refused:
        return refused


    start = time.monotonic()
    try:
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
        client = get_http_client()
        resp = await client.get("https://api.github.com/rate_limit", headers=headers, timeout=10.0)
        ms = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            remaining = resp.json().get("rate", {}).get("remaining", 0)
            return ProbeResult("github", "healthy", ms, f"Rate limit remaining: {remaining}", True, True)
        if resp.status_code in (401, 403):
            return ProbeResult("github", "auth_error", ms, f"HTTP {resp.status_code}", False)
        return ProbeResult("github", "degraded", ms, f"HTTP {resp.status_code}")
    except Exception as exc:
        return ProbeResult("github", "down", 0, str(exc)[:300])


async def probe_ocp(config: dict | None = None) -> ProbeResult:
    """Probe OpenShift/K8s API: check service account token validity."""
    from app.core.config import settings

    enabled = config.get("ocp_enabled") if config is not None else settings.OCP_ENABLED
    api_url = config.get("ocp_api_url") if config is not None else settings.OCP_API_URL
    token = config.get("ocp_sa_token") if config is not None else settings.OCP_SA_TOKEN
    namespace = (
        config.get("ocp_default_namespace")
        if config is not None
        else settings.OCP_DEFAULT_NAMESPACE
    )
    if not enabled or not api_url:
        return ProbeResult("ocp", "skipped", message="OCP_ENABLED=false or no URL configured")
    refused = _offline_hard_gate("ocp")
    if refused:
        return refused
    block = await egress_block_reason(api_url)
    if block:
        return ProbeResult("ocp", "down", 0, f"Target host is not allowed ({block})")

    start = time.monotonic()
    try:
        headers = {"Authorization": f"Bearer {token or ''}"}
        client = get_http_client()
        resp = await client.get(
            f"{api_url}/api/v1/namespaces/{namespace}",
            headers=headers,
            timeout=10.0,
        )
        ms = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            return ProbeResult("ocp", "healthy", ms, "Namespace accessible", True, True)
        if resp.status_code in (401, 403):
            return ProbeResult("ocp", "auth_error", ms, f"HTTP {resp.status_code}", False)
        return ProbeResult("ocp", "degraded", ms, f"HTTP {resp.status_code}")
    except Exception as exc:
        return ProbeResult("ocp", "down", 0, str(exc)[:300])


async def probe_slack(config: dict | None = None) -> ProbeResult:
    """Probe Slack: verify webhook or bot token connectivity."""
    from app.core.config import settings

    enabled = config.get("slack_enabled") if config is not None else settings.SLACK_ENABLED
    webhook_url = config.get("slack_webhook_url") if config is not None else settings.SLACK_WEBHOOK_URL
    if not enabled:
        return ProbeResult("slack", "skipped", message="SLACK_ENABLED=false")


    start = time.monotonic()
    try:
        # Runtime DB configuration is authoritative for global integrations.
        # A stale environment bot-token placeholder must not shadow a webhook
        # that was resolved from the encrypted settings store.
        if webhook_url:
            return ProbeResult("slack", "healthy", 0, "Webhook URL configured (no live test for webhooks)", None, None)
        if settings.SLACK_BOT_TOKEN:
            refused = await _offline_refusal("Slack", "https://slack.com/api/auth.test")
            if refused:
                return refused
            client = get_http_client()
            resp = await client.post(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"},
                timeout=10.0,
            )
            ms = int((time.monotonic() - start) * 1000)
            data = resp.json()
            if data.get("ok"):
                return ProbeResult("slack", "healthy", ms, f"Team: {data.get('team', 'OK')}", True, True)
            return ProbeResult("slack", "auth_error", ms, data.get("error", "unknown"), False)
        return ProbeResult("slack", "down", 0, "No bot token or webhook URL configured")
    except Exception as exc:
        return ProbeResult("slack", "down", 0, str(exc)[:300])


async def probe_teams(config: dict | None = None) -> ProbeResult:
    """Probe Microsoft Teams: verify webhook URL is configured."""
    from app.core.config import settings

    enabled = config.get("teams_enabled") if config is not None else settings.TEAMS_ENABLED
    webhook_url = config.get("teams_webhook_url") if config is not None else settings.TEAMS_WEBHOOK_URL
    if not enabled or not webhook_url:
        return ProbeResult("teams", "skipped", message="TEAMS_ENABLED=false or no webhook URL")
    # Teams webhooks can't be tested without sending a message; just verify config
    return ProbeResult("teams", "healthy", 0, "Webhook URL configured", None, None)


async def probe_smtp() -> ProbeResult:
    """Probe SMTP: attempt connection and auth, with the configuration the
    sender uses -- Settings -> Notifications (``smtp_config``), else the
    environment. Reading the environment alone, the probe reported
    "SMTP_ENABLED=false" for a relay configured in the UI that was delivering
    mail (homelab, 2026-10-10)."""
    from app.core.config import settings
    from app.services.notification import email_service

    try:
        cfg = await email_service._get_smtp_cfg()
    except Exception as exc:  # noqa: BLE001 -- a probe reports, it does not raise
        return ProbeResult("smtp", "down", 0, f"SMTP configuration unavailable: {type(exc).__name__}")
    if not cfg.get("enabled"):
        return ProbeResult("smtp", "skipped", message="SMTP is not enabled (Settings -> Notifications or SMTP_ENABLED)")
    host = email_service.smtp_host(cfg)
    refused = await _offline_refusal("SMTP", host)
    if refused:
        return refused

    start = time.monotonic()
    try:
        import aiosmtplib

        # The sender's TLS rule: implicit TLS, else STARTTLS (port 587).
        use_tls = bool(cfg.get("tls", True))
        smtp = aiosmtplib.SMTP(
            hostname=host, port=int(cfg.get("port") or settings.SMTP_PORT),
            use_tls=use_tls, start_tls=not use_tls,
        )
        await smtp.connect()
        if cfg.get("user") and cfg.get("password"):
            await smtp.login(cfg["user"], cfg["password"])
            auth_valid = True
        else:
            auth_valid = None
        await smtp.quit()
        ms = int((time.monotonic() - start) * 1000)
        return ProbeResult("smtp", "healthy", ms, "SMTP connection successful", auth_valid, True)
    except Exception as exc:
        ms = int((time.monotonic() - start) * 1000)
        msg = str(exc)[:300]
        if "auth" in msg.lower() or "login" in msg.lower():
            return ProbeResult("smtp", "auth_error", ms, msg, False)
        return ProbeResult("smtp", "down", ms, msg)


async def probe_ollama() -> ProbeResult:
    """Probe Ollama LLM server."""
    from app.core.config import settings

    if not settings.AI_OFFLINE_MODE:
        return ProbeResult("ollama", "skipped", message="AI_OFFLINE_MODE=false — using cloud LLM")


    start = time.monotonic()
    try:
        client = get_http_client()
        resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=5.0)
        ms = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            return ProbeResult("ollama", "healthy", ms, f"Models: {', '.join(models[:5])}", True, True)
        return ProbeResult("ollama", "degraded", ms, f"HTTP {resp.status_code}")
    except Exception as exc:
        return ProbeResult("ollama", "down", 0, str(exc)[:300])


async def probe_chromadb() -> ProbeResult:
    """Probe ChromaDB vector store."""
    from app.services.storage_config_service import get_effective_storage_config

    start = time.monotonic()
    try:
        config = await get_effective_storage_config()
        client = get_http_client()
        resp = await client.get(
            f"http://{config['chroma_host']}:{config['chroma_port']}/api/v2/heartbeat",
            timeout=4.0,
        )
        ms = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            return ProbeResult("chromadb", "healthy", ms, "ChromaDB heartbeat OK", True, True)
        return ProbeResult("chromadb", "degraded", ms, f"HTTP {resp.status_code}")
    except Exception as exc:
        return ProbeResult("chromadb", "down", 0, str(exc)[:300])


# ── All-provider probe orchestrator ──────────────────────────────────────────

ALL_PROBES = {
    "jira": probe_jira,
    "splunk": probe_splunk,
    "github": probe_github,
    "ocp": probe_ocp,
    "slack": probe_slack,
    "teams": probe_teams,
    "smtp": probe_smtp,
    "ollama": probe_ollama,
    "chromadb": probe_chromadb,
}


async def run_all_probes() -> list[ProbeResult]:
    """Run all integration probes concurrently."""
    import asyncio

    integration_cfg = None
    integration_cfg_error: Exception | None = None
    configurable = {"jira", "splunk", "github", "ocp", "slack", "teams"}
    if configurable.intersection(ALL_PROBES):
        from app.db.postgres import AsyncSessionLocal
        from app.services.integration_config_service import resolve_global_integrations

        try:
            async with AsyncSessionLocal() as db:
                integration_cfg = await resolve_global_integrations(db)
        except Exception as exc:  # noqa: BLE001 — isolate config from other probes
            integration_cfg_error = exc
            logger.warning(
                "Integration probe configuration unavailable: %s",
                exc,
            )

    async def _run_probe(provider, fn):
        if provider in configurable:
            if integration_cfg_error is not None:
                return ProbeResult(
                    provider,
                    "down",
                    0,
                    (
                        "Integration configuration unavailable: "
                        f"{type(integration_cfg_error).__name__}: "
                        f"{str(integration_cfg_error)[:240]}"
                    ),
                )
            return await fn(integration_cfg)
        return await fn()

    results = await asyncio.gather(
        *[_run_probe(provider, fn) for provider, fn in ALL_PROBES.items()],
        return_exceptions=True,
    )
    out: list[ProbeResult] = []
    for provider, result in zip(ALL_PROBES.keys(), results):
        if isinstance(result, BaseException):
            out.append(ProbeResult(provider, "down", 0, str(result)[:300]))
        else:
            out.append(result)
    return out


# ── Per-project probes (W10 INT-04) ──────────────────────────────────────────


@dataclass(frozen=True)
class _ProjectProbeTarget:
    """One enabled per-project integration, read into plain values."""

    provider: str
    project_id: uuid.UUID
    project_name: Optional[str]
    resolution: Any  # GitHubTargetResolution | GitLabTargetResolution | None (offline)


def _project_result(target: _ProjectProbeTarget, status: str, response_ms: int = 0, message: str = "",
                    auth_valid: bool | None = None, payload_valid: bool | None = None,
                    base: Optional[str] = None) -> ProbeResult:
    from app.services.github_target import target_label

    return ProbeResult(
        target.provider, status, response_ms, message, auth_valid, payload_valid,
        scope_key=str(target.project_id), project_id=str(target.project_id),
        target=target_label(base),
    )


async def _read_project_targets(
    db: "AsyncSession",
    *,
    project_ids: Optional[set[uuid.UUID]],
    providers: set[str],
    offline: bool,
) -> list[_ProjectProbeTarget]:
    """The READ phase: the enabled integrations (+ project names), then -- online
    only -- each one's target through the one resolver per provider. DB reads
    only; no HTTP. Offline, no secret is read at all."""
    from sqlalchemy import select

    from app.models.postgres import GitHubIntegration, GitLabIntegration, Project

    listed: list[tuple[str, uuid.UUID, Optional[str]]] = []
    for provider, model in (("github", GitHubIntegration), ("gitlab", GitLabIntegration)):
        if provider not in providers:
            continue
        stmt = (
            select(model.project_id, Project.name)
            .join(Project, Project.id == model.project_id)
            .where(model.enabled.is_(True))
        )
        if project_ids is not None:
            stmt = stmt.where(model.project_id.in_(project_ids))
        rows = (await db.execute(stmt.order_by(Project.name))).all()
        listed.extend((provider, row[0], row[1]) for row in rows)

    if offline:
        return [_ProjectProbeTarget(p, pid, name, None) for p, pid, name in listed]

    from app.services import github_target, gitlab_integration_service

    targets: list[_ProjectProbeTarget] = []
    for provider, pid, name in listed:
        if provider == "github":
            resolution: Any = await github_target.resolve_github_target(db, pid, check_gate=False)
        else:
            resolution = await gitlab_integration_service.load_gitlab_target(db, pid)
        targets.append(_ProjectProbeTarget(provider, pid, name, resolution))
    return targets


async def _timed_get(url: str, headers: dict[str, str]) -> tuple[Any, int]:
    start = time.monotonic()
    resp = await get_http_client().get(url, headers=headers, timeout=_PROBE_TIMEOUT)
    return resp, int((time.monotonic() - start) * 1000)


def _http_outcome(target: _ProjectProbeTarget, resp: Any, ms: int, *, base: str, healthy: str,
                  not_found: str) -> ProbeResult:
    code = resp.status_code
    if code == 200:
        return _project_result(target, "healthy", ms, healthy, True, True, base=base)
    if code in (401, 403):
        return _project_result(target, "auth_error", ms, f"HTTP {code}: the token was refused", False, base=base)
    if code == 404:
        return _project_result(target, "degraded", ms, f"HTTP 404: {not_found}", base=base)
    return _project_result(target, "degraded", ms, f"HTTP {code}", base=base)


def _exception_outcome(target: _ProjectProbeTarget, exc: BaseException, ms: int, *, base: str) -> ProbeResult:
    import httpx

    if isinstance(exc, httpx.TimeoutException):
        return _project_result(target, "timeout", ms, "Timed out", base=base)
    # The exception text, never a header: the token is not in the URL.
    return _project_result(target, "down", 0, f"{type(exc).__name__}: {str(exc)[:200]}", base=base)


async def probe_github_project(res: "GitHubTargetResolution",
                               target: Optional[_ProjectProbeTarget] = None) -> ProbeResult:
    """Probe ONE project's GitHub / GHE: ``GET {api_base}/repos/{repo}`` with its PAT."""
    target = target or _ProjectProbeTarget("github", uuid.UUID(str(res.project_id)), None, res)
    base = res.api_base or ""
    if res.target is None:
        if res.reason == "no_token":
            return _project_result(
                target, "auth_error", 0, "No token is stored for this project's GitHub integration.", False,
                base=base or None,
            )
        return _project_result(target, "down", 0, f"GitHub integration unavailable ({res.reason})",
                               base=base or None)
    gh = res.target
    url = f"{gh.api_base}/repos/{gh.repo}"
    block = await egress_block_reason(url)
    if block:
        return _project_result(target, "down", 0, f"Target host is not allowed ({block})", base=gh.api_base)
    headers = {
        "Authorization": f"Bearer {gh.pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "TestLookup/1.0",
    }
    start = time.monotonic()
    try:
        resp, ms = await _timed_get(url, headers)
    except Exception as exc:  # noqa: BLE001 -- a probe reports, it does not raise
        return _exception_outcome(target, exc, int((time.monotonic() - start) * 1000), base=gh.api_base)
    return _http_outcome(
        target, resp, ms, base=gh.api_base,
        healthy=f"Repository {gh.repo} reachable",
        not_found=f"repository {gh.repo} not found, or the token cannot see it",
    )


async def probe_gitlab_project(res: "GitLabTargetResolution",
                               target: Optional[_ProjectProbeTarget] = None) -> ProbeResult:
    """Probe ONE project's GitLab: ``GET {base}/api/v4/projects/{ref}`` with its PAT."""
    from app.services import gitlab_integration_service as gl

    target = target or _ProjectProbeTarget("gitlab", uuid.UUID(str(res.project_id)), None, res)
    base = res.base_url or ""
    if res.pat is None or not res.project_path or res.reason:
        if res.reason == "no_token":
            return _project_result(
                target, "auth_error", 0, "No token is stored for this project's GitLab integration.", False,
                base=base or None,
            )
        if res.reason == "no_project_path":
            return _project_result(
                target, "degraded", 0, "No project path is configured for this project's GitLab integration.",
                base=base or None,
            )
        return _project_result(target, "down", 0, f"GitLab integration unavailable ({res.reason})",
                               base=base or None)
    url = f"{gl._api_root(base)}/projects/{gl._project_ref(res.project_path)}"
    block = await egress_block_reason(url)
    if block:
        return _project_result(target, "down", 0, f"Target host is not allowed ({block})", base=base)
    start = time.monotonic()
    try:
        resp, ms = await _timed_get(url, gl._headers(res.pat))
    except Exception as exc:  # noqa: BLE001 -- a probe reports, it does not raise
        return _exception_outcome(target, exc, int((time.monotonic() - start) * 1000), base=base)
    return _http_outcome(
        target, resp, ms, base=base,
        healthy=f"Project {res.project_path} reachable",
        not_found=f"project {res.project_path} not found, or the token cannot see it",
    )


async def run_project_probes(
    *,
    project_ids: set[uuid.UUID] | None = None,
    providers: set[str] | None = None,
    db: "AsyncSession | None" = None,
    prune: bool = False,
) -> list[ProbeResult]:
    """Probe every ENABLED per-project GitHub / GitLab integration (W10 INT-04).

    READ phase on ONE session -- the request's ``db`` when given, else a short
    ``AsyncSessionLocal()``: the enabled integration rows (+ project names),
    filtered by ``project_ids`` (None = all) and ``providers`` (None = both).
    Offline (``AI_OFFLINE_MODE``): one ``skipped`` result per row and NO secret
    read. Online, each row's target through ``github_target.resolve_github_target
    (check_gate=False)`` / ``gitlab_integration_service.load_gitlab_target`` as
    plain values. Then the request session's connection is handed back
    (``release_read_connection``) or the short session closes.

    HTTP phase with NO session open: at most ``PROJECT_PROBE_CONCURRENCY``
    probes in flight. One result per row, each with ``scope_key = project_id =
    str(project id)`` and ``target = target_label(base)``.

    ``prune`` is accepted so the beat's full sweep says what it is; the pruning
    itself happens in ``persist_probe_results(prune=True)``, inside the write
    transaction.
    """
    from app.core.config import settings

    wanted = set(PROJECT_PROBE_PROVIDERS) if providers is None else set(providers) & set(PROJECT_PROBE_PROVIDERS)
    if not wanted or (project_ids is not None and not project_ids):
        return []
    offline = bool(settings.AI_OFFLINE_MODE)

    if db is not None:
        targets = await _read_project_targets(db, project_ids=project_ids, providers=wanted, offline=offline)
        from app.db.postgres import release_read_connection

        await release_read_connection(db)
    else:
        from app.db.postgres import AsyncSessionLocal

        async with AsyncSessionLocal() as read_db:
            targets = await _read_project_targets(
                read_db, project_ids=project_ids, providers=wanted, offline=offline,
            )

    if offline:
        return [
            _project_result(
                t, "skipped", message=f"AI_OFFLINE_MODE=true -- outbound {t.provider} calls are disabled",
            )
            for t in targets
        ]

    gate = asyncio.Semaphore(PROJECT_PROBE_CONCURRENCY)

    async def _probe(t: _ProjectProbeTarget) -> ProbeResult:
        async with gate:
            if t.provider == "github":
                return await probe_github_project(t.resolution, t)
            return await probe_gitlab_project(t.resolution, t)

    outcomes = await asyncio.gather(*(_probe(t) for t in targets), return_exceptions=True)
    results: list[ProbeResult] = []
    for t, outcome in zip(targets, outcomes):
        if isinstance(outcome, BaseException):
            results.append(_project_result(t, "down", 0, f"{type(outcome).__name__}: {str(outcome)[:200]}"))
        else:
            results.append(outcome)
    return results


async def persist_probe_results(
    results: list[ProbeResult],
    *,
    prune: bool = False,
) -> None:
    """Persist probe results to DB and update Prometheus metrics.

    Rows key on ``(provider, scope_key)`` (W10 INT-04): ``''`` is the
    deployment-wide row, a project id a per-project integration's. The GLOBAL
    gauge (``integration_health_gauge``, labelled by provider only) is staged
    for deployment-wide results only; the per-project rows are counted into
    ``integration_project_health_gauge`` per (provider, status) after the
    commit -- 12 series whatever the number of projects, never a project
    label. ``prune=True`` (the beat's full sweep only) deletes, in the same
    transaction, the per-project rows whose integration is no longer enabled
    (removed, disabled).
    """
    from sqlalchemy import select

    from app.core.metrics import integration_health_gauge
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import IntegrationHealthCheck, IntegrationProbeResult

    now = datetime.now(timezone.utc)
    metric_updates: list[tuple[str, float]] = []

    async with AsyncSessionLocal() as db:
        for r in results:
            if r.status == "skipped":
                # A skip used to `continue` outright, which left the provider's
                # PREVIOUS row completely untouched — status and
                # ``last_checked_at`` included. So a provider that stopped
                # being probed kept advertising its last verdict forever.
                #
                # Measured live: ``AI_OFFLINE_MODE`` flipped to false on
                # 2026-08-16, from which point ollama was skipped every cycle.
                # Five days later /integration-health/status still reported
                # ``ollama: healthy`` with ``last_checked_at`` frozen at
                # 2026-08-16 — for a service that had zero models installed.
                # A health page reporting OK because it stopped looking is the
                # worst version of a health page.
                #
                # The eight never-configured providers (jira, slack, smtp, ...)
                # had no row at all, so they were invisible rather than
                # visibly-not-monitored. The frontend already styles a
                # ``skipped`` badge — that state was simply unreachable.
                #
                # NO history row: skips are not probe outcomes, and counting
                # them would corrupt ``uptime_pct`` on the trends tab.
                # ``consecutive_failures`` and ``last_success_at`` are left
                # alone for the same reason — a skip is not a failure.
                existing = await db.execute(
                    select(IntegrationHealthCheck).where(
                        IntegrationHealthCheck.provider == r.provider,
                        IntegrationHealthCheck.scope_key == r.scope_key,
                    )
                )
                hc = existing.scalar_one_or_none()
                if hc is None:
                    hc = IntegrationHealthCheck(provider=r.provider, scope_key=r.scope_key)
                    db.add(hc)
                hc.status = "skipped"
                hc.last_checked_at = now
                hc.message = r.message
                hc.response_ms = None

                # A one-hot status is multiprocess-safe: prometheus_client does
                # not implement Gauge.remove() in multiprocess mode, so absence
                # cannot reliably represent "skipped". The provider-labelled
                # gauge is the deployment-wide row's only; a project's row is
                # counted into the per-(provider, status) gauge after commit.
                if not r.scope_key:
                    metric_updates.append(
                        (r.provider, INTEGRATION_HEALTH_VALUES["skipped"])
                    )
                continue

            # Insert history record
            db.add(IntegrationProbeResult(
                provider=r.provider,
                scope_key=r.scope_key,
                status=r.status,
                response_ms=r.response_ms,
                message=r.message,
                auth_valid=r.auth_valid,
                payload_valid=r.payload_valid,
            ))

            # Upsert latest status
            existing = await db.execute(
                select(IntegrationHealthCheck).where(
                    IntegrationHealthCheck.provider == r.provider,
                    IntegrationHealthCheck.scope_key == r.scope_key,
                )
            )
            hc = existing.scalar_one_or_none()
            if hc is None:
                hc = IntegrationHealthCheck(provider=r.provider, scope_key=r.scope_key)
                db.add(hc)

            hc.status = r.status
            hc.last_checked_at = now
            hc.message = r.message
            hc.response_ms = r.response_ms

            if r.status == "healthy":
                hc.consecutive_failures = 0
                hc.last_success_at = now
            else:
                hc.consecutive_failures = (hc.consecutive_failures or 0) + 1

            # Stage the Prometheus gauge update until the transaction commits.
            # Resolve the declared status before commit. An unknown producer
            # value must not commit history and then fail while publishing it.
            gauge_value = INTEGRATION_HEALTH_VALUES[r.status]
            if not r.scope_key:
                metric_updates.append((r.provider, gauge_value))

        if prune:
            await _prune_project_rows(db)

        await db.commit()

        # The per-project gauge, from the committed table: ONE grouped read.
        project_counts = await _project_status_counts(db)

    for provider, gauge_value in metric_updates:
        integration_health_gauge.labels(provider=provider).set(gauge_value)
    _set_project_gauge(project_counts)

    # Check for alerts
    await _check_alerts(results)


async def _prune_project_rows(db: "AsyncSession") -> None:
    """Delete the per-project rows whose integration is no longer enabled.

    Stage-only (the caller's transaction commits). The deployment-wide rows
    (``scope_key = ''``) are never touched; a project row survives only while
    its provider's integration for that project exists and is enabled.
    """
    from sqlalchemy import String, cast, delete, select

    from app.models.postgres import GitHubIntegration, GitLabIntegration, IntegrationHealthCheck

    for provider, model in (("github", GitHubIntegration), ("gitlab", GitLabIntegration)):
        enabled_scopes = select(cast(model.project_id, String)).where(model.enabled.is_(True))
        await db.execute(
            delete(IntegrationHealthCheck)
            .where(
                IntegrationHealthCheck.provider == provider,
                IntegrationHealthCheck.scope_key != "",
                IntegrationHealthCheck.scope_key.not_in(enabled_scopes),
            )
            .execution_options(synchronize_session=False)
        )


async def _project_status_counts(db: "AsyncSession") -> dict[tuple[str, str], int]:
    """``{(provider, status): rows}`` over the per-project health rows."""
    from sqlalchemy import func, select

    from app.models.postgres import IntegrationHealthCheck

    rows = (
        await db.execute(
            select(
                IntegrationHealthCheck.provider,
                IntegrationHealthCheck.status,
                func.count().label("count"),
            )
            .where(IntegrationHealthCheck.scope_key != "")
            .group_by(IntegrationHealthCheck.provider, IntegrationHealthCheck.status)
        )
    ).all()
    return {(row[0], row[1]): int(row[2]) for row in rows}


def _set_project_gauge(counts: dict[tuple[str, str], int]) -> None:
    """Every (provider, status) pair is set, a missing pair to 0, so a
    recovered state reads 0 rather than its last count."""
    from app.core.metrics import integration_project_health_gauge

    for provider in PROJECT_PROBE_PROVIDERS:
        for status in INTEGRATION_HEALTH_VALUES:
            integration_project_health_gauge.labels(provider=provider, status=status).set(
                counts.get((provider, status), 0)
            )


async def _check_alerts(results: list[ProbeResult]) -> None:
    """Alert if any provider has sustained failures."""
    from sqlalchemy import select

    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import IntegrationHealthCheck

    async with AsyncSessionLocal() as db:
        for r in results:
            if r.status == "skipped" or r.status == "healthy":
                continue

            existing = await db.execute(
                select(IntegrationHealthCheck).where(
                    IntegrationHealthCheck.provider == r.provider,
                    IntegrationHealthCheck.scope_key == r.scope_key,
                )
            )
            hc = existing.scalar_one_or_none()
            if hc and (hc.consecutive_failures or 0) >= ALERT_THRESHOLD:
                if r.scope_key:
                    logger.warning(
                        "ALERT: Integration %s of project %s has %d consecutive failures. Status: %s. Message: %s",
                        r.provider, r.project_id or r.scope_key, hc.consecutive_failures, r.status, r.message,
                    )
                    continue
                logger.warning(
                    "ALERT: Integration %s has %d consecutive failures. Status: %s. Message: %s",
                    r.provider, hc.consecutive_failures, r.status, r.message,
                )
                # In production, this would also dispatch a notification via the notification manager.
                # For now, we log at WARNING level which will be captured by the observability stack.
