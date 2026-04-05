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

logger = logging.getLogger("services.integration_probe")

ALERT_THRESHOLD = 3  # consecutive failures before alerting


@dataclass
class ProbeResult:
    provider: str
    status: str  # healthy | degraded | down | auth_error | timeout
    response_ms: int = 0
    message: str = ""
    auth_valid: bool | None = None
    payload_valid: bool | None = None


async def probe_jira() -> ProbeResult:
    """Probe Jira REST API: check auth and server info."""
    from app.core.config import settings

    if not settings.JIRA_ENABLED or not settings.JIRA_DOMAIN:
        return ProbeResult("jira", "skipped", message="JIRA_ENABLED=false or no domain configured")

    import httpx

    start = time.monotonic()
    try:
        url = f"https://{settings.JIRA_DOMAIN}/rest/api/3/serverInfo"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, auth=(settings.JIRA_EMAIL or "", settings.JIRA_API_TOKEN or ""))
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

    import httpx

    start = time.monotonic()
    try:
        url = f"{settings.SPLUNK_BASE_URL}/services/server/info"
        headers = {"Authorization": f"Bearer {settings.SPLUNK_API_TOKEN}"}
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            resp = await client.get(url, headers=headers)
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

    import httpx

    start = time.monotonic()
    try:
        headers = {"Authorization": f"token {settings.GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://api.github.com/rate_limit", headers=headers)
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

    import httpx

    start = time.monotonic()
    try:
        headers = {"Authorization": f"Bearer {settings.OCP_SA_TOKEN}"}
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            resp = await client.get(f"{settings.OCP_API_URL}/api/v1/namespaces/{settings.OCP_DEFAULT_NAMESPACE}", headers=headers)
        ms = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            return ProbeResult("ocp", "healthy", ms, "Namespace accessible", True, True)
        if resp.status_code in (401, 403):
            return ProbeResult("ocp", "auth_error", ms, f"HTTP {resp.status_code}", False)
        return ProbeResult("ocp", "degraded", ms, f"HTTP {resp.status_code}")
    except Exception as exc:
        return ProbeResult("ocp", "down", 0, str(exc)[:300])


async def probe_slack() -> ProbeResult:
    """Probe Slack: verify webhook or bot token connectivity."""
    from app.core.config import settings

    if not settings.SLACK_ENABLED:
        return ProbeResult("slack", "skipped", message="SLACK_ENABLED=false")

    import httpx

    start = time.monotonic()
    try:
        if settings.SLACK_BOT_TOKEN:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://slack.com/api/auth.test",
                    headers={"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"},
                )
            ms = int((time.monotonic() - start) * 1000)
            data = resp.json()
            if data.get("ok"):
                return ProbeResult("slack", "healthy", ms, f"Team: {data.get('team', 'OK')}", True, True)
            return ProbeResult("slack", "auth_error", ms, data.get("error", "unknown"), False)
        elif settings.SLACK_WEBHOOK_URL:
            return ProbeResult("slack", "healthy", 0, "Webhook URL configured (no live test for webhooks)", None, None)
        return ProbeResult("slack", "down", 0, "No bot token or webhook URL configured")
    except Exception as exc:
        return ProbeResult("slack", "down", 0, str(exc)[:300])


async def probe_teams() -> ProbeResult:
    """Probe Microsoft Teams: verify webhook URL is configured."""
    from app.core.config import settings

    if not settings.TEAMS_ENABLED or not settings.TEAMS_WEBHOOK_URL:
        return ProbeResult("teams", "skipped", message="TEAMS_ENABLED=false or no webhook URL")
    # Teams webhooks can't be tested without sending a message; just verify config
    return ProbeResult("teams", "healthy", 0, "Webhook URL configured", None, None)


async def probe_smtp() -> ProbeResult:
    """Probe SMTP: attempt connection and auth."""
    from app.core.config import settings

    if not settings.SMTP_ENABLED:
        return ProbeResult("smtp", "skipped", message="SMTP_ENABLED=false")

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

    import httpx

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
        ms = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            return ProbeResult("ollama", "healthy", ms, f"Models: {', '.join(models[:5])}", True, True)
        return ProbeResult("ollama", "degraded", ms, f"HTTP {resp.status_code}")
    except Exception as exc:
        return ProbeResult("ollama", "down", 0, str(exc)[:300])


async def probe_chromadb() -> ProbeResult:
    """Probe ChromaDB vector store."""
    from app.core.config import settings

    import httpx

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(f"{settings.chroma_host_url}/api/v2/heartbeat")
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

    results = await asyncio.gather(
        *[fn() for fn in ALL_PROBES.values()],
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

    async with AsyncSessionLocal() as db:
        for r in results:
            if r.status == "skipped":
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

            # Update Prometheus gauge
            gauge_value = 1.0 if r.status == "healthy" else 0.5 if r.status == "degraded" else 0.0
            integration_health_gauge.labels(provider=r.provider).set(gauge_value)

        await db.commit()

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
