"""
Unified Audit Dashboard Service — cross-table audit queries with redaction (OPS-04).

Queries across all 5 audit tables, applies tenant isolation, and redacts
sensitive values before returning results.
"""
from __future__ import annotations

import csv
import io
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import Float, cast as sa_cast, false as sa_false, func, select
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
    User,
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
    allowed_project_ids: set[uuid.UUID] | None = None,
) -> dict:
    """
    Query audit events across all tables with tenant isolation.

    ``allowed_project_ids`` is the caller's membership set, or ``None`` for
    ADMIN (unrestricted). It is the ONLY thing enforcing tenant isolation here
    — ``project_id`` is a user-supplied *filter*, not a permission.

    Two of the five sources have no ``project_id`` column at all
    (``SettingsAuditLog``, ``IdentityEvent``): they record instance-wide
    changes such as settings edits and SSO/SCIM events. They cannot be scoped
    to a project, so for a restricted caller they are omitted entirely rather
    than leaked — the alternative would be handing every QA_LEAD the whole
    instance's settings history.

    Returns {total, items: [{source, action, actor_name, project_id, detail, created_at}]}
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    restricted = allowed_project_ids is not None
    # D-0018: every matching row counts in ``total`` (one COUNT per source), and each source returns its first
    # ``page * page_size`` rows, so the merged slice for page N is complete and the count is not a cap.
    fetch = page * page_size
    total = 0
    # (created_at, source, row id, item): the row id breaks a tie in created_at, so pages are stable.
    collected: list[tuple[str, str, str, dict]] = []

    async def _collect(query, model, source: str, to_item: Callable[[Any], dict]) -> None:
        nonlocal total
        total += (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
        result = await db.execute(query.order_by(model.created_at.desc(), model.id.desc()).limit(fetch))
        for row in result.scalars().all():
            item = to_item(row)
            collected.append((item.get("created_at") or "", source, str(row.id), item))

    def _scope(query, column):
        """Restrict a project-scoped table to the caller's memberships.

        Rows with a NULL project_id are dropped for a restricted caller: a
        project-scoped table with no project cannot be proven to belong to
        them.
        """
        if not restricted:
            return query
        if not allowed_project_ids:
            return query.where(sa_false())
        return query.where(column.in_(allowed_project_ids))

    # D-0018: each AccessAuditLog row is in exactly one category, so All is the disjoint union of the
    # categories: an action starting with the literal ``report_`` is a report event, any other an access
    # event. ``autoescape`` makes the ``_`` literal (LIKE with an ESCAPE character; it was a wildcard).
    report_action = AccessAuditLog.action.startswith("report_", autoescape=True)

    def _access_item(row: AccessAuditLog, source: str) -> dict:
        return {
            "source": source,
            "action": row.action,
            "actor_name": row.actor_name,
            "actor_id": str(row.actor_user_id) if row.actor_user_id else None,
            "project_id": str(row.project_id) if row.project_id else None,
            "detail": redact_dict(row.after_value) if redact else row.after_value,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    # ── Access audit ────────────────────────────────────────────────────
    if not category or category == "access":
        access_query = _scope(
            select(AccessAuditLog).where(AccessAuditLog.created_at >= cutoff).where(~report_action),
            AccessAuditLog.project_id,
        )
        if project_id:
            access_query = access_query.where(AccessAuditLog.project_id == project_id)
        if actor_id:
            access_query = access_query.where(AccessAuditLog.actor_user_id == actor_id)
        await _collect(access_query, AccessAuditLog, "access", lambda row: _access_item(row, "access"))

    # ── Settings audit ──────────────────────────────────────────────────
    # Instance-wide source with no project column — ADMIN only (see docstring).
    if (not category or category == "settings") and not restricted:
        settings_query = select(SettingsAuditLog).where(SettingsAuditLog.created_at >= cutoff)
        if actor_id:
            settings_query = settings_query.where(SettingsAuditLog.actor_id == actor_id)
        await _collect(settings_query, SettingsAuditLog, "settings", lambda row: {
            "source": "settings",
            "action": row.action,
            "actor_name": row.actor_name,
            "actor_id": str(row.actor_id) if row.actor_id else None,
            "project_id": None,
            "detail": {"setting_key": row.setting_key, "changed_fields": row.changed_fields},
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    # ── Test management audit ───────────────────────────────────────────
    if not category or category == "test_management":
        test_mgmt_query = _scope(
            select(TestCaseAuditLog).where(TestCaseAuditLog.created_at >= cutoff),
            TestCaseAuditLog.project_id,
        )
        if project_id:
            test_mgmt_query = test_mgmt_query.where(TestCaseAuditLog.project_id == project_id)
        if actor_id:
            test_mgmt_query = test_mgmt_query.where(TestCaseAuditLog.actor_id == actor_id)
        await _collect(test_mgmt_query, TestCaseAuditLog, "test_management", lambda row: {
            "source": "test_management",
            "action": row.action,
            "actor_name": row.actor_name,
            "actor_id": str(row.actor_id) if row.actor_id else None,
            "project_id": str(row.project_id) if row.project_id else None,
            "detail": {"entity_type": row.entity_type, "entity_id": str(row.entity_id)},
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    # ── Identity events ─────────────────────────────────────────────────
    # Instance-wide source with no project column — ADMIN only (see docstring).
    if (not category or category == "identity") and not restricted:
        identity_query = select(IdentityEvent).where(IdentityEvent.created_at >= cutoff)
        if actor_id:
            identity_query = identity_query.where(IdentityEvent.actor_id == actor_id)
        await _collect(identity_query, IdentityEvent, "identity", lambda row: {
            "source": "identity",
            "action": str(row.event_type),
            "actor_name": row.actor_name,
            "actor_id": str(row.actor_id) if row.actor_id else None,
            "project_id": None,
            "detail": redact_dict(row.detail) if redact else row.detail,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "success": row.success,
        })

    # ── Report export/share events (AccessAuditLog rows whose action starts with ``report_``) ─
    if not category or category == "report":
        report_query = _scope(
            select(AccessAuditLog).where(AccessAuditLog.created_at >= cutoff).where(report_action),
            AccessAuditLog.project_id,
        )
        if project_id:
            report_query = report_query.where(AccessAuditLog.project_id == project_id)
        if actor_id:
            report_query = report_query.where(AccessAuditLog.actor_user_id == actor_id)
        await _collect(report_query, AccessAuditLog, "report", lambda row: _access_item(row, "report"))

    # Newest first across the sources; equal times in a fixed order (source, then row id).
    collected.sort(key=lambda entry: entry[:3], reverse=True)
    start = (page - 1) * page_size
    items = [entry[3] for entry in collected[start:start + page_size]]
    await _name_actors_consistently(db, items)
    return {"total": total, "items": items}


async def _name_actors_consistently(db: AsyncSession, items: list[dict]) -> None:
    """D-78: one person reads the same on every row.

    Each audit writer snapshots a different attribute (``username`` for access,
    ``username or full_name`` for settings, ``full_name or username`` for test
    management), so one person read "priya.nair" on one row and "Priya Nair"
    on the next. A live account's rows now show its current ``full_name or
    username``. The stored snapshot is kept beside it as
    ``actor_name_recorded``, and an erased or deleted account keeps the stored
    name (the compliance purpose this view is kept for; see
    ``tests/test_erased_actor_projection.py``).
    """
    ids: set[uuid.UUID] = set()
    for item in items:
        item.setdefault("actor_name_recorded", item.get("actor_name"))
        raw = item.get("actor_id")
        if raw:
            try:
                ids.add(uuid.UUID(str(raw)))
            except ValueError:
                continue
    if not ids:
        return
    rows = (
        await db.execute(
            select(User.id, User.full_name, User.username).where(
                User.id.in_(ids), User.erased_at.is_(None)
            )
        )
    ).all()
    names = {str(row[0]): (row[1] or row[2]) for row in rows if (row[1] or row[2])}
    for item in items:
        current = names.get(str(item.get("actor_id") or ""))
        if current:
            item["actor_name"] = current


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
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["source", "action", "actor_name", "project_id", "created_at"])
    for item in result["items"]:
        values = [
            item["source"],
            item["action"],
            item.get("actor_name", ""),
            item.get("project_id", ""),
            item.get("created_at", ""),
        ]
        writer.writerow([_csv_text(value) for value in values])
    return output.getvalue()


def _csv_text(value: object) -> str:
    """Keep exported audit values structurally valid and spreadsheet-inert."""
    text = str(value or "")
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text
