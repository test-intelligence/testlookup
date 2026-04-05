"""
Value Metrics Service — quantifies operational value delivered by the platform.

Aggregates data from existing tables to produce customer-facing metrics:
  - Triage time saved (clusters with multiple tests → grouped investigations)
  - Defects auto-grouped (failure clusters with size ≥ 2)
  - Duplicate tickets avoided (defect candidates flagged as duplicate)
  - Flaky tests identified (from flaky_coach_results)
  - Risky releases blocked (release decisions with NO_GO recommendation)
  - Intelligence reports generated (from run_intelligence_snapshots)
  - Release overrides (human overrides of AI recommendations)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    Defect,
    DefectCandidate,
    FailureCluster,
    FlakyCoachResult,
    ReleaseDecision,
    RunIntelligenceSnapshot,
    TestRun,
)

logger = logging.getLogger("services.value_metrics")

# Estimated minutes saved per triage action (configurable)
_MINUTES_PER_CLUSTER_TRIAGE = 15       # investigating a cluster vs individual tests
_MINUTES_PER_DUPLICATE_AVOIDED = 30    # avoiding a duplicate Jira ticket round-trip
_MINUTES_PER_INTELLIGENCE_REPORT = 20  # vs manual root cause analysis


async def get_value_metrics(
    db: AsyncSession,
    project_id: Optional[uuid.UUID] = None,
    days: int = 30,
) -> dict:
    """
    Compute operational value metrics for a project (or all projects) over a time window.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # ── Defects auto-grouped (clusters with ≥ 2 members) ────────────────────
    cluster_stmt = select(sa_func.count(FailureCluster.id)).where(
        FailureCluster.created_at >= cutoff,
        FailureCluster.size >= 2,
    )
    if project_id:
        cluster_stmt = cluster_stmt.join(TestRun, FailureCluster.test_run_id == TestRun.id).where(TestRun.project_id == project_id)
    defects_grouped = (await db.execute(cluster_stmt)).scalar() or 0

    # Total tests grouped (sum of member_count across clusters)
    tests_grouped_stmt = select(sa_func.coalesce(sa_func.sum(FailureCluster.size), 0)).where(
        FailureCluster.created_at >= cutoff,
        FailureCluster.size >= 2,
    )
    if project_id:
        tests_grouped_stmt = tests_grouped_stmt.join(TestRun, FailureCluster.test_run_id == TestRun.id).where(TestRun.project_id == project_id)
    tests_grouped = (await db.execute(tests_grouped_stmt)).scalar() or 0

    # ── Duplicate tickets avoided ────────────────────────────────────────────
    dup_stmt = select(sa_func.count(Defect.id)).where(
        Defect.created_at >= cutoff,
        Defect.is_duplicate.is_(True),
    )
    if project_id:
        dup_stmt = dup_stmt.where(Defect.project_id == project_id)
    duplicates_avoided = (await db.execute(dup_stmt)).scalar() or 0

    # Also count candidates flagged as duplicate (not yet promoted)
    dup_cand_stmt = select(sa_func.count(DefectCandidate.id)).where(
        DefectCandidate.created_at >= cutoff,
        DefectCandidate.is_duplicate.is_(True),
    )
    if project_id:
        dup_cand_stmt = dup_cand_stmt.join(TestRun, DefectCandidate.run_id == TestRun.id).where(TestRun.project_id == project_id)
    dup_candidates = (await db.execute(dup_cand_stmt)).scalar() or 0
    total_duplicates_avoided = duplicates_avoided + dup_candidates

    # ── Defects promoted ─────────────────────────────────────────────────────
    promoted_stmt = select(sa_func.count(Defect.id)).where(
        Defect.created_at >= cutoff,
        Defect.promotion_source == "cluster_promotion",
    )
    if project_id:
        promoted_stmt = promoted_stmt.where(Defect.project_id == project_id)
    defects_promoted = (await db.execute(promoted_stmt)).scalar() or 0

    # ── Flaky tests identified ───────────────────────────────────────────────
    flaky_stmt = select(sa_func.count(FlakyCoachResult.id))
    if project_id:
        flaky_stmt = flaky_stmt.where(FlakyCoachResult.project_id == project_id)
    flaky_identified = (await db.execute(flaky_stmt)).scalar() or 0

    quarantine_stmt = select(sa_func.count(FlakyCoachResult.id)).where(
        FlakyCoachResult.quarantine_recommendation == "QUARANTINE",
    )
    if project_id:
        quarantine_stmt = quarantine_stmt.where(FlakyCoachResult.project_id == project_id)
    quarantine_recommended = (await db.execute(quarantine_stmt)).scalar() or 0

    # ── Risky releases blocked ───────────────────────────────────────────────
    blocked_stmt = select(sa_func.count(ReleaseDecision.id)).where(
        ReleaseDecision.created_at >= cutoff,
        ReleaseDecision.recommendation == "NO_GO",
    )
    if project_id:
        blocked_stmt = blocked_stmt.join(TestRun, ReleaseDecision.test_run_id == TestRun.id).where(TestRun.project_id == project_id)
    releases_blocked = (await db.execute(blocked_stmt)).scalar() or 0

    conditional_stmt = select(sa_func.count(ReleaseDecision.id)).where(
        ReleaseDecision.created_at >= cutoff,
        ReleaseDecision.recommendation == "CONDITIONAL_GO",
    )
    if project_id:
        conditional_stmt = conditional_stmt.join(TestRun, ReleaseDecision.test_run_id == TestRun.id).where(TestRun.project_id == project_id)
    releases_conditional = (await db.execute(conditional_stmt)).scalar() or 0

    overrides_stmt = select(sa_func.count(ReleaseDecision.id)).where(
        ReleaseDecision.created_at >= cutoff,
        ReleaseDecision.human_override.isnot(None),
    )
    if project_id:
        overrides_stmt = overrides_stmt.join(TestRun, ReleaseDecision.test_run_id == TestRun.id).where(TestRun.project_id == project_id)
    release_overrides = (await db.execute(overrides_stmt)).scalar() or 0

    # ── Intelligence reports ─────────────────────────────────────────────────
    intel_stmt = select(sa_func.count(RunIntelligenceSnapshot.id)).where(
        RunIntelligenceSnapshot.created_at >= cutoff,
    )
    if project_id:
        intel_stmt = intel_stmt.join(TestRun, RunIntelligenceSnapshot.run_id == TestRun.id).where(TestRun.project_id == project_id)
    intelligence_reports = (await db.execute(intel_stmt)).scalar() or 0

    # ── Triage time saved estimate ───────────────────────────────────────────
    triage_minutes_saved = (
        defects_grouped * _MINUTES_PER_CLUSTER_TRIAGE
        + total_duplicates_avoided * _MINUTES_PER_DUPLICATE_AVOIDED
        + intelligence_reports * _MINUTES_PER_INTELLIGENCE_REPORT
    )

    return {
        "period_days": days,
        "project_id": str(project_id) if project_id else None,
        "triage_time_saved_minutes": triage_minutes_saved,
        "triage_time_saved_hours": round(triage_minutes_saved / 60, 1),
        "defects_auto_grouped": defects_grouped,
        "tests_grouped": tests_grouped,
        "duplicate_tickets_avoided": total_duplicates_avoided,
        "defects_promoted": defects_promoted,
        "flaky_tests_identified": flaky_identified,
        "quarantine_recommended": quarantine_recommended,
        "risky_releases_blocked": releases_blocked,
        "releases_conditional": releases_conditional,
        "release_overrides": release_overrides,
        "intelligence_reports_generated": intelligence_reports,
    }
