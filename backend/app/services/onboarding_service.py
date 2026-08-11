"""
Onboarding Service — manages setup wizard progress and usage event tracking.

Onboarding steps:
  1. create_project    — at least one project exists
  2. upload_run        — at least one test run ingested
  3. connect_jira      — Jira integration configured
  4. connect_telemetry — at least one telemetry integration (Splunk, OCP, Slack)
  5. view_intelligence — user has opened Run Intelligence for a run
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func as sa_func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    ProductUsageEvent,
    TenantOnboardingStatus,
    TestRun,
)

logger = logging.getLogger("services.onboarding")

ONBOARDING_STEPS = [
    {"key": "create_project", "label": "Create a Project", "description": "Set up your first test project"},
    {"key": "upload_run", "label": "Upload Test Results", "description": "Ingest a test run via webhook or client SDK"},
    {"key": "connect_jira", "label": "Connect Jira", "description": "Link Jira for defect promotion"},
    {"key": "connect_telemetry", "label": "Connect Telemetry", "description": "Add Splunk, OCP, or Slack integration"},
    {"key": "view_intelligence", "label": "View Run Intelligence", "description": "Open the AI analysis for a test run"},
]


def _build_default_status(project_id: uuid.UUID) -> dict:
    """Build a default onboarding status when the table is not yet available."""
    steps = [
        {**s, "status": "pending", "completed_at": None}
        for s in ONBOARDING_STEPS
    ]
    return {
        "project_id": str(project_id),
        "steps": steps,
        "completed_count": 0,
        "total_count": len(steps),
        "progress_pct": 0,
        "is_complete": False,
    }


async def _table_exists(db: AsyncSession) -> bool:
    """Check whether the tenant_onboarding_status table exists."""
    try:
        result = await db.execute(
            text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'tenant_onboarding_status')")
        )
        return bool(result.scalar())
    except Exception:
        return False


async def get_onboarding_status(
    project_id: uuid.UUID,
    db: AsyncSession,
) -> dict:
    """Return the onboarding progress for a project."""
    if not await _table_exists(db):
        return _build_default_status(project_id)

    try:
        result = await db.execute(
            select(TenantOnboardingStatus).where(
                TenantOnboardingStatus.project_id == project_id
            )
        )
        existing = {s.step_key: s for s in result.scalars().all()}
    except Exception as exc:
        logger.warning("Failed to query onboarding status: %s", exc)
        return _build_default_status(project_id)

    steps = []
    for step_def in ONBOARDING_STEPS:
        record = existing.get(step_def["key"])
        steps.append({
            "key": step_def["key"],
            "label": step_def["label"],
            "description": step_def["description"],
            "status": record.status if record else "pending",
            "completed_at": record.completed_at.isoformat() if record and record.completed_at else None,
        })

    completed = sum(1 for s in steps if s["status"] in ("completed", "skipped"))
    total = len(steps)

    return {
        "project_id": str(project_id),
        "steps": steps,
        "completed_count": completed,
        "total_count": total,
        "progress_pct": round(completed / total * 100) if total else 0,
        "is_complete": completed == total,
    }


async def complete_step(
    project_id: uuid.UUID,
    step_key: str,
    user_id: Optional[uuid.UUID],
    db: AsyncSession,
) -> dict:
    """Mark an onboarding step as completed. Idempotent."""
    valid_keys = {s["key"] for s in ONBOARDING_STEPS}
    if step_key not in valid_keys:
        raise ValueError(f"Invalid step key: {step_key}")

    if not await _table_exists(db):
        return _build_default_status(project_id)

    result = await db.execute(
        select(TenantOnboardingStatus).where(
            TenantOnboardingStatus.project_id == project_id,
            TenantOnboardingStatus.step_key == step_key,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        if existing.status != "completed":
            existing.status = "completed"
            existing.completed_at = datetime.now(timezone.utc)
            existing.completed_by = user_id
    else:
        db.add(TenantOnboardingStatus(
            project_id=project_id,
            step_key=step_key,
            status="completed",
            completed_at=datetime.now(timezone.utc),
            completed_by=user_id,
        ))

    # Item #2: stage only. The router handler commits.
    await db.flush()
    return await get_onboarding_status(project_id, db)


async def skip_step(
    project_id: uuid.UUID,
    step_key: str,
    db: AsyncSession,
) -> dict:
    """Mark an onboarding step as skipped. Stage-only; handler commits."""
    valid_keys = {s["key"] for s in ONBOARDING_STEPS}
    if step_key not in valid_keys:
        raise ValueError(f"Invalid step key: {step_key}")

    if not await _table_exists(db):
        return _build_default_status(project_id)

    result = await db.execute(
        select(TenantOnboardingStatus).where(
            TenantOnboardingStatus.project_id == project_id,
            TenantOnboardingStatus.step_key == step_key,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.status = "skipped"
    else:
        db.add(TenantOnboardingStatus(
            project_id=project_id,
            step_key=step_key,
            status="skipped",
        ))

    await db.flush()
    return await get_onboarding_status(project_id, db)


async def auto_detect_progress(
    project_id: uuid.UUID,
    db: AsyncSession,
) -> dict:
    """Auto-detect which onboarding steps are already complete by checking DB state.

    Stage-only: the router handler commits (or rolls back on failure). The
    service flushes between auto-complete steps so subsequent queries see
    the intermediate state in the same transaction.
    """
    if not await _table_exists(db):
        logger.info("Onboarding table not yet created — returning defaults")
        return _build_default_status(project_id)

    # Auto-complete create_project (it obviously exists if we're here)
    await _auto_complete_if_pending(db, project_id, "create_project")
    await db.flush()

    # Check if project has runs
    run_count_result = await db.execute(
        select(sa_func.count(TestRun.id)).where(TestRun.project_id == project_id)
    )
    has_runs = (run_count_result.scalar() or 0) > 0
    if has_runs:
        await _auto_complete_if_pending(db, project_id, "upload_run")
        await db.flush()

    # Check integration config — Jira and telemetry share one AppSetting row.
    from app.models.postgres import AppSetting
    integrations_result = await db.execute(
        select(AppSetting).where(AppSetting.key == "integrations_config")
    )
    integrations_row = integrations_result.scalar_one_or_none()
    integrations = (integrations_row.value if integrations_row else None) or {}

    if integrations.get("jira_enabled"):
        await _auto_complete_if_pending(db, project_id, "connect_jira")
        await db.flush()

    # connect_telemetry is satisfied by any of Splunk / OCP / Slack being
    # enabled (mirrors the step description "Add Splunk, OCP, or Slack
    # integration"). Without this, a self-hoster who wires up telemetry never
    # gets credit for the step and it stays pending on the setup wizard.
    if any(integrations.get(k) for k in ("splunk_enabled", "ocp_enabled", "slack_enabled")):
        await _auto_complete_if_pending(db, project_id, "connect_telemetry")
        await db.flush()

    return await get_onboarding_status(project_id, db)


async def _auto_complete_if_pending(
    db: AsyncSession,
    project_id: uuid.UUID,
    step_key: str,
) -> None:
    result = await db.execute(
        select(TenantOnboardingStatus).where(
            TenantOnboardingStatus.project_id == project_id,
            TenantOnboardingStatus.step_key == step_key,
        )
    )
    existing = result.scalar_one_or_none()
    if not existing:
        db.add(TenantOnboardingStatus(
            project_id=project_id,
            step_key=step_key,
            status="completed",
            completed_at=datetime.now(timezone.utc),
        ))
    elif existing.status == "pending":
        existing.status = "completed"
        existing.completed_at = datetime.now(timezone.utc)


# ── Usage Event Tracking ─────────────────────────────────────────────────────

async def track_event(
    db: AsyncSession,
    event_name: str,
    user_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    payload: Optional[dict] = None,
) -> None:
    """Stage a product usage event row.

    Fire-and-forget semantics: the service swallows staging errors (e.g.
    table missing in a partially-migrated env) so callers don't have to
    wrap usage tracking in try/except. The router handler owns the actual
    commit; if commit fails, that's a harder error the caller can surface
    as needed.
    """
    try:
        db.add(ProductUsageEvent(
            user_id=user_id,
            project_id=project_id,
            event_name=event_name,
            event_payload=payload,
        ))
    except Exception as exc:
        logger.warning("Failed to stage event %s: %s", event_name, exc)


async def get_usage_events(
    db: AsyncSession,
    event_name: Optional[str] = None,
    project_id: Optional[uuid.UUID] = None,
    limit: int = 100,
) -> list[dict]:
    """Retrieve recent usage events for analytics."""
    try:
        stmt = (
            select(ProductUsageEvent)
            .order_by(ProductUsageEvent.created_at.desc())
            .limit(limit)
        )
        if event_name:
            stmt = stmt.where(ProductUsageEvent.event_name == event_name)
        if project_id:
            stmt = stmt.where(ProductUsageEvent.project_id == project_id)

        result = await db.execute(stmt)
        return [
            {
                "id": str(e.id),
                "user_id": str(e.user_id) if e.user_id else None,
                "project_id": str(e.project_id) if e.project_id else None,
                "event_name": e.event_name,
                "event_payload": e.event_payload,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in result.scalars().all()
        ]
    except Exception as exc:
        logger.warning("Failed to query usage events: %s", exc)
        return []
