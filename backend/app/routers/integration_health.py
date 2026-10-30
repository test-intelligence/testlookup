"""Integration Health router — dashboard, on-demand probe, history (OPS-01).

W10 INT-04: health rows are keyed ``(provider, scope_key)``. ``scope_key = ''``
is a deployment-wide row (every existing consumer reads only those: the
default ``GET /status``, the trends, a history read without ``project_id``);
a project's GitHub / GitLab integration is probed and stored under its project
id. ``GET /status?scope=all`` adds one row per enabled per-project integration
the reader may see -- a never-probed one reads ``probed: false`` /
``status: "unknown"``, never healthy -- and one row per outbound webhook the
circuit breaker paused. A QUERY ``project_id`` (probe, history) passes
``resolve_project_scope`` before anything is read.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_accessible_project_ids, require_role, resolve_project_scope
from app.db.postgres import get_db, release_read_connection
from app.models.postgres import (
    GitHubIntegration,
    GitLabIntegration,
    IntegrationHealthCheck,
    IntegrationProbeResult,
    Project,
    User,
    UserRole,
    WebhookSubscription,
)
from app.models.schemas import IntegrationHealthRow

logger = logging.getLogger("routers.integration_health")

router = APIRouter(prefix="/api/v1/integration-health", tags=["Integration Health"])

#: ``webhook_breaker.WEBHOOK_DISABLED_REASONS[0]``, written out: a router that
#: imports the breaker module (which records activity) would read as recording
#: activity itself to ``backend.activity-coverage``.
_CIRCUIT_OPEN = "circuit_open"


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _global_row(hc: Any) -> dict[str, Any]:
    return {
        "provider": hc.provider,
        "scope_key": "",
        "kind": "global",
        "status": hc.status,
        "last_checked_at": _iso(hc.last_checked_at),
        "message": hc.message,
        "response_ms": hc.response_ms,
        "consecutive_failures": hc.consecutive_failures or 0,
        "last_success_at": _iso(hc.last_success_at),
    }


def _host_of(url: Optional[str]) -> Optional[str]:
    """Host (and port) of a webhook target: never a path, a query or a user name."""
    try:
        netloc = urlsplit(url or "").netloc
    except ValueError:
        return None
    return netloc.rsplit("@", 1)[-1] or None


async def _hand_back(db: Any) -> None:
    """Release the request session's read connection before a second session
    or an HTTP call. A test double without session state is left alone."""
    if all(hasattr(db, name) for name in ("new", "dirty", "deleted", "close")):
        await release_read_connection(db)


async def _project_rows(db: AsyncSession, accessible: Optional[set[uuid.UUID]]) -> list[dict[str, Any]]:
    """One row per ENABLED per-project integration the reader may see.

    Reads: the GitHub list, the GitLab list (each with the project's name), the
    health rows of those ids -- three statements, none per row."""
    from app.services.github_target import target_label
    from app.services.integration_probe_service import NOT_PROBED_MESSAGE

    if accessible is not None and not accessible:
        return []
    github = (
        select(GitHubIntegration.project_id, Project.name, GitHubIntegration.api_base_url)
        .join(Project, Project.id == GitHubIntegration.project_id)
        .where(GitHubIntegration.enabled.is_(True))
    )
    gitlab = (
        select(GitLabIntegration.project_id, Project.name, GitLabIntegration.base_url)
        .join(Project, Project.id == GitLabIntegration.project_id)
        .where(GitLabIntegration.enabled.is_(True))
    )
    if accessible is not None:
        github = github.where(GitHubIntegration.project_id.in_(accessible))
        gitlab = gitlab.where(GitLabIntegration.project_id.in_(accessible))
    integrations: list[tuple[str, uuid.UUID, Optional[str], Optional[str]]] = []
    for provider, stmt in (("github", github), ("gitlab", gitlab)):
        for listed in (await db.execute(stmt.order_by(Project.name))).all():
            integrations.append((provider, listed[0], listed[1], listed[2]))
    if not integrations:
        return []

    scope_keys = sorted({str(pid) for _, pid, _, _ in integrations})
    health = {
        (hc.provider, hc.scope_key): hc
        for hc in (
            await db.execute(
                select(IntegrationHealthCheck).where(
                    IntegrationHealthCheck.provider.in_(("github", "gitlab")),
                    IntegrationHealthCheck.scope_key.in_(scope_keys),
                )
            )
        ).scalars().all()
    }
    rows: list[dict[str, Any]] = []
    for provider, pid, name, base in integrations:
        hc = health.get((provider, str(pid)))
        row: dict[str, Any] = {
            "provider": provider,
            "scope_key": str(pid),
            "kind": "project",
            "project_id": pid,
            "project_name": name,
            "label": name,
            "target": target_label(base),
        }
        if hc is None:
            # Absence is not health: never probed reads "unknown", not healthy.
            row.update(status="unknown", probed=False, message=NOT_PROBED_MESSAGE)
        else:
            row.update(
                status=hc.status,
                probed=True,
                last_checked_at=_iso(hc.last_checked_at),
                message=hc.message,
                response_ms=hc.response_ms,
                consecutive_failures=hc.consecutive_failures or 0,
                last_success_at=_iso(hc.last_success_at),
            )
        rows.append(row)
    return rows


async def _paused_webhook_rows(
    db: AsyncSession, accessible: Optional[set[uuid.UUID]],
) -> list[dict[str, Any]]:
    """One row per outbound webhook the circuit breaker paused (one read)."""
    if accessible is not None and not accessible:
        return []
    stmt = (
        select(WebhookSubscription, Project.name)
        .join(Project, Project.id == WebhookSubscription.project_id)
        .where(
            WebhookSubscription.disabled_reason == _CIRCUIT_OPEN,
            WebhookSubscription.enabled.is_(False),
        )
    )
    if accessible is not None:
        stmt = stmt.where(WebhookSubscription.project_id.in_(accessible))
    rows: list[dict[str, Any]] = []
    for sub, project_name in (await db.execute(stmt.order_by(Project.name, WebhookSubscription.name))).all():
        rows.append({
            "provider": "webhook",
            "scope_key": str(sub.id),
            "kind": "webhook",
            "project_id": sub.project_id,
            "project_name": project_name,
            "label": sub.name,
            "target": _host_of(sub.target_url),
            "status": "paused",
            "probed": True,
            "last_checked_at": _iso(sub.disabled_at),
            "message": (sub.last_error or "")[:300] or None,
            "consecutive_failures": sub.consecutive_failures or 0,
            "breaker_threshold": sub.breaker_threshold,
        })
    return rows


@router.get("/status", response_model=list[IntegrationHealthRow])
async def get_all_status(
    scope: str = "global",
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Latest health per integration.

    ``scope=global`` (the default, every existing consumer): the
    deployment-wide rows. ``scope=all``: those, then one row per enabled
    per-project GitHub / GitLab integration of the reader's projects (an admin:
    every project), then one row per paused outbound webhook in them. Five
    reads for ``all``, none per row.
    """
    from app.services.integration_probe_service import HEALTH_SCOPES

    if scope not in HEALTH_SCOPES:
        raise HTTPException(status_code=422, detail="scope must be global or all")
    result = await db.execute(
        select(IntegrationHealthCheck)
        .where(IntegrationHealthCheck.scope_key == "")
        .order_by(IntegrationHealthCheck.provider)
    )
    rows: list[dict[str, Any]] = [_global_row(hc) for hc in result.scalars().all()]
    if scope == "global":
        return rows
    accessible = await get_accessible_project_ids(db, current_user)
    rows.extend(await _project_rows(db, accessible))
    rows.extend(await _paused_webhook_rows(db, accessible))
    return rows


def _probe_answer(results: list) -> list[dict[str, Any]]:
    return [
        {
            "provider": r.provider,
            "status": r.status,
            "response_ms": r.response_ms,
            "message": r.message,
            "auth_valid": r.auth_valid,
            "payload_valid": r.payload_valid,
            "scope_key": getattr(r, "scope_key", ""),
            "project_id": getattr(r, "project_id", None),
        }
        for r in results
    ]


@router.post("/probe")
async def trigger_probe(
    provider: Optional[str] = None,
    project_id: Optional[uuid.UUID] = None,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Trigger an on-demand health probe for all or a specific provider (QA_LEAD+).

    ``project_id`` probes that project's GitHub or GitLab integration
    (``provider=github|gitlab``); the id passes ``resolve_project_scope`` before
    anything is read. Without a provider and a project: every deployment-wide
    probe plus every per-project integration the reader may see. The request
    session's connection is handed back before any probe makes an HTTP call.
    """
    from app.services.integration_probe_service import (
        ALL_PROBES,
        PROJECT_PROBE_PROVIDERS,
        SAVED_CONFIG_PROBES,
        persist_probe_results,
        run_project_probes,
    )

    if project_id is not None:
        # FIRST: the query id is checked before any read (another tenant's id
        # is a 403, indistinguishable from a missing project).
        await resolve_project_scope(db, current_user, str(project_id))
        if provider not in PROJECT_PROBE_PROVIDERS:
            raise HTTPException(
                status_code=422,
                detail="A per-project probe needs provider=github or provider=gitlab.",
            )
        results = await run_project_probes(project_ids={project_id}, providers={provider}, db=db)
        if not results:
            label = "GitHub" if provider == "github" else "GitLab"
            raise HTTPException(status_code=404, detail=f"This project has no enabled {label} integration.")
        await persist_probe_results(results)
        return _probe_answer(results)

    supported = set(ALL_PROBES) | set(SAVED_CONFIG_PROBES) | set(PROJECT_PROBE_PROVIDERS)
    if provider and provider not in supported:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown integration provider: {provider}. "
                f"Supported: {', '.join(sorted(supported))}"
            ),
        )
    if provider == "gitlab":
        raise HTTPException(status_code=422, detail="GitLab is probed per project: pass project_id.")

    if provider and provider in SAVED_CONFIG_PROBES:
        # W23d AGT-15: the saved-only Prometheus block, merged over the global config, on the request session;
        # the connection is handed back before the probe's HTTP call.
        from app.services.integration_config_service import (
            resolve_global_integrations,
            resolve_prometheus_settings,
        )

        merged_cfg = {**await resolve_global_integrations(db), **await resolve_prometheus_settings(db)}
        await _hand_back(db)
        results = [await SAVED_CONFIG_PROBES[provider](merged_cfg)]
    elif provider and provider in ALL_PROBES:
        if provider in {"jira", "splunk", "github", "ocp", "slack", "teams"}:
            from app.services.integration_config_service import (
                resolve_global_integrations,
            )

            integration_cfg = await resolve_global_integrations(db)
            await _hand_back(db)
            results = [await ALL_PROBES[provider](integration_cfg)]
        else:
            await _hand_back(db)
            results = [await ALL_PROBES[provider]()]
    else:
        from app.services.integration_probe_service import run_all_probes

        accessible = await get_accessible_project_ids(db, current_user)
        await _hand_back(db)
        results = await run_all_probes()
        results += await run_project_probes(project_ids=accessible, db=db)

    await persist_probe_results(results)

    return _probe_answer(results)


@router.get("/history/{provider}")
async def get_provider_history(
    provider: str,
    days: int = Query(default=7, ge=1, le=90),
    project_id: Optional[uuid.UUID] = None,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Probe history for a provider: the deployment-wide history, or -- with
    ``project_id`` (checked by ``resolve_project_scope`` first) -- that
    project's integration."""
    if project_id is not None:
        await resolve_project_scope(db, current_user, str(project_id))
    scope_key = str(project_id) if project_id is not None else ""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(IntegrationProbeResult)
        .where(
            IntegrationProbeResult.provider == provider,
            IntegrationProbeResult.scope_key == scope_key,
            IntegrationProbeResult.checked_at >= cutoff,
        )
        .order_by(IntegrationProbeResult.checked_at.desc())
        .limit(500)
    )
    probes = result.scalars().all()
    return [
        {
            "id": str(p.id),
            "status": p.status,
            "response_ms": p.response_ms,
            "message": p.message,
            "auth_valid": p.auth_valid,
            "payload_valid": p.payload_valid,
            "checked_at": p.checked_at.isoformat() if p.checked_at else None,
        }
        for p in probes
    ]


@router.get("/trends")
async def get_health_trends(
    days: int = Query(default=7, ge=1, le=30),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated health trends for all providers over a period.

    Every status the probe service persists gets its own counter. The probers
    emit five (``skipped`` is dropped before insert, see
    ``integration_probe_service._persist``), and reporting only three left
    ``auth_error`` and ``timeout`` probes counted in ``total_probes`` — so they
    dragged ``uptime_pct`` down — while appearing in no column at all. A
    provider whose token had expired rendered as 0% uptime with 0 healthy,
    0 degraded and 0 down, which reads as "no data" rather than "your
    credentials are wrong".

    The trend is deployment-wide (W10 INT-04): per-project probes
    (``scope_key <> ''``) stay out of both statements.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Count by provider and status
    result = await db.execute(
        select(
            IntegrationProbeResult.provider,
            IntegrationProbeResult.status,
            func.count(IntegrationProbeResult.id).label("count"),
        )
        .where(
            IntegrationProbeResult.checked_at >= cutoff,
            IntegrationProbeResult.scope_key == "",
        )
        .group_by(IntegrationProbeResult.provider, IntegrationProbeResult.status)
    )
    rows = result.all()

    # Average latency, one row per provider. Computed separately rather than
    # per-status: the per-status averages cannot be combined without their
    # weights, and the previous code simply let the last group win — an
    # order-dependent value, since a GROUP BY has no defined row order.
    # ``response_ms = 0`` is the sentinel a ``down`` probe records when it never
    # got a response (`ProbeResult(provider, "down", 0, ...)`), not a 0ms
    # measurement, so those rows are excluded from the average instead of
    # deflating it.
    latency = await db.execute(
        select(
            IntegrationProbeResult.provider,
            func.avg(IntegrationProbeResult.response_ms).label("avg_ms"),
        )
        .where(
            IntegrationProbeResult.checked_at >= cutoff,
            IntegrationProbeResult.scope_key == "",
            IntegrationProbeResult.response_ms > 0,
        )
        .group_by(IntegrationProbeResult.provider)
    )
    avg_by_provider = {r.provider: r.avg_ms for r in latency.all()}

    trends: dict[str, dict] = {}
    for row in rows:
        provider = row.provider
        if provider not in trends:
            trends[provider] = {
                "provider": provider,
                "total_probes": 0,
                "healthy": 0,
                "degraded": 0,
                "down": 0,
                "timeout": 0,
                "auth_error": 0,
                "avg_response_ms": 0,
            }
        trends[provider][row.status] = trends[provider].get(row.status, 0) + row.count
        trends[provider]["total_probes"] += row.count

    # Calculate uptime percentage
    for provider, t in trends.items():
        total = t["total_probes"]
        if total > 0:
            t["uptime_pct"] = round((t.get("healthy", 0) / total) * 100, 1)
        else:
            t["uptime_pct"] = 0
        avg_ms = avg_by_provider.get(provider)
        t["avg_response_ms"] = round(float(avg_ms), 0) if avg_ms is not None else 0

    return list(trends.values())
