"""
Integration Probe Service — active health probes for external systems (OPS-01).

Probes: Jira, Splunk, GitHub, OCP/K8s, Slack, Teams, SMTP, Ollama, ChromaDB.
Records auth validity, latency, payload correctness.
Alerts on sustained failures (consecutive_failures >= threshold).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.http_client import get_http_client

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


@dataclass
class ProbeResult:
    provider: str
    status: str  # healthy | degraded | down | auth_error | timeout
    response_ms: int = 0
    message: str = ""
    auth_valid: bool | None = None
    payload_valid: bool | None = None


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


async def probe_jira() -> ProbeResult:
    """Probe Jira REST API: check auth and server info."""
    from app.core.config import settings

    if not settings.JIRA_ENABLED or not settings.JIRA_DOMAIN:
        return ProbeResult("jira", "skipped", message="JIRA_ENABLED=false or no domain configured")
    refused = _offline_hard_gate("jira")
    if refused:
        return refused

    import httpx

    start = time.monotonic()
    try:
        url = f"https://{settings.JIRA_DOMAIN}/rest/api/3/serverInfo"
        client = get_http_client()
        resp = await client.get(
            url,
            auth=(settings.JIRA_EMAIL or "", settings.JIRA_API_TOKEN or ""),
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


async def probe_splunk() -> ProbeResult:
    """Probe Splunk REST API: check auth and service availability."""
    from app.core.config import settings

    if not settings.SPLUNK_ENABLED or not settings.SPLUNK_BASE_URL:
        return ProbeResult("splunk", "skipped", message="SPLUNK_ENABLED=false or no URL configured")
    refused = _offline_hard_gate("splunk")
    if refused:
        return refused

    import httpx

    start = time.monotonic()
    try:
        url = f"{settings.SPLUNK_BASE_URL}/services/server/info"
        headers = {"Authorization": f"Bearer {settings.SPLUNK_API_TOKEN}"}
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


async def probe_github() -> ProbeResult:
    """Probe GitHub API: check token validity."""
    from app.core.config import settings

    if not settings.GITHUB_TOKEN:
        return ProbeResult("github", "skipped", message="No GITHUB_TOKEN configured")
    refused = _offline_hard_gate("github")
    if refused:
        return refused


    start = time.monotonic()
    try:
        headers = {"Authorization": f"token {settings.GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
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


async def probe_ocp() -> ProbeResult:
    """Probe OpenShift/K8s API: check service account token validity."""
    from app.core.config import settings

    if not settings.OCP_ENABLED or not settings.OCP_API_URL:
        return ProbeResult("ocp", "skipped", message="OCP_ENABLED=false or no URL configured")
    refused = _offline_hard_gate("ocp")
    if refused:
        return refused


    start = time.monotonic()
    try:
        headers = {"Authorization": f"Bearer {settings.OCP_SA_TOKEN}"}
        client = get_http_client()
        resp = await client.get(
            f"{settings.OCP_API_URL}/api/v1/namespaces/{settings.OCP_DEFAULT_NAMESPACE}",
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
    """Probe SMTP: attempt connection and auth."""
    from app.core.config import settings

    if not settings.SMTP_ENABLED:
        return ProbeResult("smtp", "skipped", message="SMTP_ENABLED=false")
    refused = await _offline_refusal("SMTP", settings.SMTP_HOST)
    if refused:
        return refused

    start = time.monotonic()
    try:
        import aiosmtplib

        smtp = aiosmtplib.SMTP(hostname=settings.SMTP_HOST, port=settings.SMTP_PORT, use_tls=settings.SMTP_TLS)
        await smtp.connect()
        if settings.SMTP_USER and settings.SMTP_PASSWORD:
            await smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
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

    notification_cfg = None
    notification_cfg_error: Exception | None = None
    if "slack" in ALL_PROBES or "teams" in ALL_PROBES:
        from app.db.postgres import AsyncSessionLocal
        from app.services.integration_config_service import resolve_global_notification_webhooks

        try:
            async with AsyncSessionLocal() as db:
                notification_cfg = await resolve_global_notification_webhooks(db)
        except Exception as exc:  # noqa: BLE001 — isolate config from other probes
            notification_cfg_error = exc
            logger.warning(
                "Notification integration probe configuration unavailable: %s",
                exc,
            )

    async def _run_probe(provider, fn):
        if provider in ("slack", "teams"):
            if notification_cfg_error is not None:
                return ProbeResult(
                    provider,
                    "down",
                    0,
                    (
                        "Notification configuration unavailable: "
                        f"{type(notification_cfg_error).__name__}: "
                        f"{str(notification_cfg_error)[:240]}"
                    ),
                )
            return await fn(notification_cfg)
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


async def persist_probe_results(
    results: list[ProbeResult],
) -> None:
    """Persist probe results to DB and update Prometheus metrics."""
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
                        IntegrationHealthCheck.provider == r.provider
                    )
                )
                hc = existing.scalar_one_or_none()
                if hc is None:
                    hc = IntegrationHealthCheck(provider=r.provider)
                    db.add(hc)
                hc.status = "skipped"
                hc.last_checked_at = now
                hc.message = r.message
                hc.response_ms = None

                # A one-hot status is multiprocess-safe: prometheus_client does
                # not implement Gauge.remove() in multiprocess mode, so absence
                # cannot reliably represent "skipped".
                metric_updates.append(
                    (r.provider, INTEGRATION_HEALTH_VALUES["skipped"])
                )
                continue

            # Insert history record
            db.add(IntegrationProbeResult(
                provider=r.provider,
                status=r.status,
                response_ms=r.response_ms,
                message=r.message,
                auth_valid=r.auth_valid,
                payload_valid=r.payload_valid,
            ))

            # Upsert latest status
            existing = await db.execute(
                select(IntegrationHealthCheck).where(IntegrationHealthCheck.provider == r.provider)
            )
            hc = existing.scalar_one_or_none()
            if hc is None:
                hc = IntegrationHealthCheck(provider=r.provider)
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
            metric_updates.append((r.provider, INTEGRATION_HEALTH_VALUES[r.status]))

        await db.commit()

    for provider, gauge_value in metric_updates:
        integration_health_gauge.labels(provider=provider).set(gauge_value)

    # Check for alerts
    await _check_alerts(results)


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
                select(IntegrationHealthCheck).where(IntegrationHealthCheck.provider == r.provider)
            )
            hc = existing.scalar_one_or_none()
            if hc and (hc.consecutive_failures or 0) >= ALERT_THRESHOLD:
                logger.warning(
                    "ALERT: Integration %s has %d consecutive failures. Status: %s. Message: %s",
                    r.provider, hc.consecutive_failures, r.status, r.message,
                )
                # In production, this would also dispatch a notification via the notification manager.
                # For now, we log at WARNING level which will be captured by the observability stack.
