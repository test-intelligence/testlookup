"""
Unified Audit Dashboard Service — cross-table audit queries with redaction (OPS-04).

Queries across all 5 audit tables, applies tenant isolation, and redacts
sensitive values before returning results.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import cast

from sqlalchemy import Float, cast as sa_cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    AccessAuditLog,
    AIAnalysis,
    IdentityEvent,
    ReleaseDecision,
    SettingsAuditLog,
    TestCase,
    TestCaseAuditLog,
    TestRun,
)

from app.services.redaction_service import redact_dict, redact_value  # noqa: F401

logger = logging.getLogger("services.audit_dashboard")


# ── Unified audit query ─────────────────────────────────────────────────────

# Audit event categories for filtering
AUDIT_CATEGORIES = {
    "access": "Role and membership changes",
    "settings": "Configuration and secret changes",
    "test_management": "Test case lifecycle actions",
    "identity": "SSO, SCIM, and authentication events",
    "notification": "Notification dispatch events",
    "release": "Release decision overrides",
    "report": "Report export and share events",
}


async def query_unified_audit(
    db: AsyncSession,
    project_id: uuid.UUID | None = None,
    category: str | None = None,
    actor_id: uuid.UUID | None = None,
    days: int = 30,
    page: int = 1,
    page_size: int = 50,
    redact: bool = True,
) -> dict:
    """
    Query audit events across all tables with tenant isolation.

    Returns {total, items: [{source, action, actor_name, project_id, detail, created_at}]}
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    items: list[dict] = []

    # ── Access audit ────────────────────────────────────────────────────
    if not category or category == "access":
        access_query = select(AccessAuditLog).where(AccessAuditLog.created_at >= cutoff)
        if project_id:
            access_query = access_query.where(AccessAuditLog.project_id == project_id)
        if actor_id:
            access_query = access_query.where(AccessAuditLog.actor_user_id == actor_id)
        access_result = await db.execute(access_query.order_by(AccessAuditLog.created_at.desc()).limit(page_size))
        access_rows = cast(list[AccessAuditLog], access_result.scalars().all())
        for access_row in access_rows:
            items.append({
                "source": "access",
                "action": access_row.action,
                "actor_name": access_row.actor_name,
                "actor_id": str(access_row.actor_user_id) if access_row.actor_user_id else None,
                "project_id": str(access_row.project_id) if access_row.project_id else None,
                "detail": redact_dict(access_row.after_value) if redact else access_row.after_value,
                "created_at": access_row.created_at.isoformat() if access_row.created_at else None,
            })

    # ── Settings audit ──────────────────────────────────────────────────
    if not category or category == "settings":
        settings_query = select(SettingsAuditLog).where(SettingsAuditLog.created_at >= cutoff)
        if actor_id:
            settings_query = settings_query.where(SettingsAuditLog.actor_id == actor_id)
        settings_result = await db.execute(settings_query.order_by(SettingsAuditLog.created_at.desc()).limit(page_size))
        settings_rows = cast(list[SettingsAuditLog], settings_result.scalars().all())
        for settings_row in settings_rows:
            items.append({
                "source": "settings",
                "action": settings_row.action,
                "actor_name": settings_row.actor_name,
                "actor_id": str(settings_row.actor_id) if settings_row.actor_id else None,
                "project_id": None,
                "detail": {"setting_key": settings_row.setting_key, "changed_fields": settings_row.changed_fields},
                "created_at": settings_row.created_at.isoformat() if settings_row.created_at else None,
            })

    # ── Test management audit ───────────────────────────────────────────
    if not category or category == "test_management":
        test_mgmt_query = select(TestCaseAuditLog).where(TestCaseAuditLog.created_at >= cutoff)
        if project_id:
            test_mgmt_query = test_mgmt_query.where(TestCaseAuditLog.project_id == project_id)
        if actor_id:
            test_mgmt_query = test_mgmt_query.where(TestCaseAuditLog.actor_id == actor_id)
        test_mgmt_result = await db.execute(test_mgmt_query.order_by(TestCaseAuditLog.created_at.desc()).limit(page_size))
        test_mgmt_rows = cast(list[TestCaseAuditLog], test_mgmt_result.scalars().all())
        for test_mgmt_row in test_mgmt_rows:
            items.append({
                "source": "test_management",
                "action": test_mgmt_row.action,
                "actor_name": test_mgmt_row.actor_name,
                "actor_id": str(test_mgmt_row.actor_id) if test_mgmt_row.actor_id else None,
                "project_id": str(test_mgmt_row.project_id) if test_mgmt_row.project_id else None,
                "detail": {"entity_type": test_mgmt_row.entity_type, "entity_id": str(test_mgmt_row.entity_id)},
                "created_at": test_mgmt_row.created_at.isoformat() if test_mgmt_row.created_at else None,
            })

    # ── Identity events ─────────────────────────────────────────────────
    if not category or category == "identity":
        identity_query = select(IdentityEvent).where(IdentityEvent.created_at >= cutoff)
        if actor_id:
            identity_query = identity_query.where(IdentityEvent.actor_id == actor_id)
        identity_result = await db.execute(identity_query.order_by(IdentityEvent.created_at.desc()).limit(page_size))
        identity_rows = cast(list[IdentityEvent], identity_result.scalars().all())
        for identity_row in identity_rows:
            detail = redact_dict(identity_row.detail) if redact else identity_row.detail
            items.append({
                "source": "identity",
                "action": str(identity_row.event_type),
                "actor_name": identity_row.actor_name,
                "actor_id": str(identity_row.actor_id) if identity_row.actor_id else None,
                "project_id": None,
                "detail": detail,
                "created_at": identity_row.created_at.isoformat() if identity_row.created_at else None,
                "success": identity_row.success,
            })

    # ── Report export/share events (from AccessAuditLog with report_ prefix) ─
    if not category or category == "report":
        report_query = (
            select(AccessAuditLog)
            .where(AccessAuditLog.created_at >= cutoff)
            .where(AccessAuditLog.action.like("report_%"))
        )
        if project_id:
            report_query = report_query.where(AccessAuditLog.project_id == project_id)
        report_result = await db.execute(report_query.order_by(AccessAuditLog.created_at.desc()).limit(page_size))
        report_rows = cast(list[AccessAuditLog], report_result.scalars().all())
        for report_row in report_rows:
            items.append({
                "source": "report",
                "action": report_row.action,
                "actor_name": report_row.actor_name,
                "actor_id": str(report_row.actor_user_id) if report_row.actor_user_id else None,
                "project_id": str(report_row.project_id) if report_row.project_id else None,
                "detail": report_row.after_value,
                "created_at": report_row.created_at.isoformat() if report_row.created_at else None,
            })

    # Sort all items by created_at descending
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    # Paginate
    total = len(items)
    start = (page - 1) * page_size
    items = items[start:start + page_size]

    return {"total": total, "items": items}


# ── Tenant-scoped observability ──────────────────────────────────────────────


async def get_tenant_observability(
    db: AsyncSession,
    project_id: uuid.UUID,
    days: int = 7,
) -> dict:
    """Get tenant-scoped observability metrics for a project."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Test runs — total/avg/sum AND the FAILED count in one aggregate query
    # (a conditional COUNT ... FILTER) instead of a second COUNT over the same
    # (project_id, created_at >= cutoff) window. FILTER (WHERE status='FAILED')
    # counts exactly the rows the separate ``status == 'FAILED'`` query did
    # (NULL status is excluded by both), so the result is identical.
    runs_result = await db.execute(
        select(
            func.count(TestRun.id).label("total_runs"),
            func.avg(sa_cast(TestRun.pass_rate, Float)).label("avg_pass_rate"),
            func.sum(TestRun.total_tests).label("total_tests"),
            func.count(TestRun.id)
            .filter(TestRun.status == "FAILED")
            .label("failed_runs"),
        )
        .where(TestRun.project_id == project_id, TestRun.created_at >= cutoff)
    )
    run_stats = runs_result.one_or_none()
    failed_runs = (run_stats.failed_runs or 0) if run_stats else 0

    # AI analyses
    ai_result = await db.execute(
        select(func.count(AIAnalysis.id))
        .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
        .join(TestRun, TestCase.test_run_id == TestRun.id)
        .where(TestRun.project_id == project_id, AIAnalysis.created_at >= cutoff)
    )
    ai_count = ai_result.scalar() or 0

    # Release decisions
    release_result = await db.execute(
        select(func.count(ReleaseDecision.id))
        .join(TestRun, ReleaseDecision.test_run_id == TestRun.id)
        .where(TestRun.project_id == project_id, ReleaseDecision.created_at >= cutoff)
    )
    release_count = release_result.scalar() or 0

    # Audit events for this project
    audit_count_result = await db.execute(
        select(func.count(AccessAuditLog.id))
        .where(AccessAuditLog.project_id == project_id, AccessAuditLog.created_at >= cutoff)
    )
    audit_count = audit_count_result.scalar() or 0

    return {
        "project_id": str(project_id),
        "period_days": days,
        "total_runs": run_stats.total_runs if run_stats else 0,
        "total_tests": run_stats.total_tests if run_stats else 0,
        "avg_pass_rate": round(float(run_stats.avg_pass_rate), 1) if run_stats and run_stats.avg_pass_rate else None,
        "failed_runs": failed_runs,
        "ai_analyses_count": ai_count,
        "release_decisions_count": release_count,
        "audit_events_count": audit_count,
    }


async def export_audit_csv(
    db: AsyncSession,
    project_id: uuid.UUID | None = None,
    category: str | None = None,
    days: int = 30,
) -> str:
    """Export audit events as CSV with redaction applied."""
    result = await query_unified_audit(
        db, project_id=project_id, category=category, days=days,
        page=1, page_size=5000, redact=True,
    )
    lines = ["source,action,actor_name,project_id,created_at"]
    for item in result["items"]:
        lines.append(
            f"{item['source']},{item['action']},{item.get('actor_name', '')},{item.get('project_id', '')},{item.get('created_at', '')}"
        )
    return "\n".join(lines)
