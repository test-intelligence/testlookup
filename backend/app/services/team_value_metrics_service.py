"""
Team-level value metrics split — Tier 2 item 11.

Wraps the existing ``value_metrics_service`` with a per-team partitioner
driven by ``ServiceOwnershipRule``. The caller picks a project and a
time window; the service enumerates accessible teams from ownership
rules and produces one row per team containing the subset of metrics
that can be attributed to tests matching that team's ownership glob.

No schema changes — everything is computed on demand from existing
tables. The UI can add a team filter dropdown without a backfill.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from typing import Any, Optional

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    Defect,
    ServiceOwnershipRule,
    TestCase,
    TestRun,
    TestStatus,
)

logger = logging.getLogger("services.team_value_metrics")


# Minutes of engineering time saved by each category of automation
# action. Mirrors the constants in ``value_metrics_service`` so the
# workspace and team-level numbers agree on their conversion rates.
_MINUTES_PER_CLUSTER_TRIAGE = 15
_MINUTES_PER_DUPLICATE_AVOIDED = 30


def _match_owner(
    suite_name: Optional[str],
    test_name: Optional[str],
    rules: list[ServiceOwnershipRule],
) -> Optional[str]:
    """Return the owner name for a test by walking ownership rules.

    Rules are evaluated in priority order (highest first). The first
    glob match wins — matches ``criticality_service.resolve_owner``'s
    resolution behaviour so team-level metrics agree with release
    gate routing.
    """
    haystack = " ".join(filter(None, [suite_name or "", test_name or ""])).lower()
    for rule in rules:
        pattern = (rule.match_pattern or "").lower()
        if not pattern:
            continue
        if fnmatch(haystack, pattern) or fnmatch(suite_name or "", rule.match_pattern or ""):
            return rule.team_name or rule.service_name
    return None


async def get_team_value_metrics(
    db: AsyncSession,
    project_id: uuid.UUID,
    days: int = 30,
) -> dict[str, Any]:
    """Partition value metrics by owning team.

    Returns a dict with a top-level ``teams`` list. Each entry holds
    the metrics attributable to that team plus a ``test_count`` so the
    UI can show relative contribution.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Load the project's ownership rules once.
    rules_result = await db.execute(
        select(ServiceOwnershipRule)
        .where(
            ServiceOwnershipRule.project_id == project_id,
            ServiceOwnershipRule.is_active.is_(True),
        )
        .order_by(ServiceOwnershipRule.priority.desc())
    )
    rules = list(rules_result.scalars().all())
    if not rules:
        return {
            "project_id": project_id,
            "period_days": days,
            "teams": [],
            "note": (
                "No ServiceOwnershipRule rows configured for this project. "
                "Configure ownership rules to see per-team value metrics."
            ),
        }

    # Load every TestCase in the window for this project. The rule set
    # is typically small enough (< 50 rules) to evaluate in Python —
    # SQL-side glob evaluation is awkward and the join key is the same.
    # For very large projects (> 100k tests per window) we'd partition
    # by suite_name first, but that's a follow-up optimisation.
    tc_result = await db.execute(
        select(
            TestCase.id,
            TestCase.suite_name,
            TestCase.test_name,
            TestCase.status,
            TestCase.duration_ms,
        )
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(
            TestRun.project_id == project_id,
            TestCase.created_at >= cutoff,
        )
        .limit(50_000)
    )

    team_buckets: dict[str, dict[str, Any]] = {}
    for row in tc_result.all():
        owner = _match_owner(row.suite_name, row.test_name, rules)
        if not owner:
            owner = "Unassigned"
        bucket = team_buckets.setdefault(owner, {
            "team": owner,
            "test_count": 0,
            "passed_count": 0,
            "failed_count": 0,
            "broken_count": 0,
            "flaky_count": 0,
            "total_duration_ms": 0,
            "defect_count": 0,
            "mttr_hours": None,
            "estimated_minutes_saved": 0,
        })
        bucket["test_count"] += 1
        status = (row.status or "").upper()
        if status == TestStatus.PASSED.value:
            bucket["passed_count"] += 1
        elif status == TestStatus.FAILED.value:
            bucket["failed_count"] += 1
        elif status == TestStatus.BROKEN.value:
            bucket["broken_count"] += 1
        if row.duration_ms:
            bucket["total_duration_ms"] += int(row.duration_ms)

    # Count defects per team — walk Defect → TestCase → (suite, name).
    # One join per team keeps the query bounded; we batch by joining
    # all defects in the window once and classifying them in Python.
    defect_result = await db.execute(
        select(
            Defect.id,
            Defect.test_case_id,
            Defect.created_at,
            Defect.resolved_at,
            TestCase.suite_name,
            TestCase.test_name,
        )
        .join(TestCase, TestCase.id == Defect.test_case_id)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(
            TestRun.project_id == project_id,
            Defect.created_at >= cutoff,
        )
    )
    # Per-team running MTTR accumulator: (sum_hours, count).
    mttr_accum: dict[str, tuple[float, int]] = {}
    for d in defect_result.all():
        owner = _match_owner(d.suite_name, d.test_name, rules) or "Unassigned"
        bucket = team_buckets.setdefault(owner, {
            "team": owner,
            "test_count": 0,
            "passed_count": 0,
            "failed_count": 0,
            "broken_count": 0,
            "flaky_count": 0,
            "total_duration_ms": 0,
            "defect_count": 0,
            "mttr_hours": None,
            "estimated_minutes_saved": 0,
        })
        bucket["defect_count"] += 1
        if d.resolved_at and d.created_at:
            delta_hours = (d.resolved_at - d.created_at).total_seconds() / 3600.0
            if delta_hours >= 0:
                prev = mttr_accum.get(owner, (0.0, 0))
                mttr_accum[owner] = (prev[0] + delta_hours, prev[1] + 1)

    # Finalize MTTR + estimated-time-saved rollup per team.
    for team, accum in mttr_accum.items():
        if accum[1] > 0:
            team_buckets[team]["mttr_hours"] = round(accum[0] / accum[1], 2)

    for team, bucket in team_buckets.items():
        # Same rate card as the workspace service so numbers agree.
        bucket["estimated_minutes_saved"] = (
            bucket["defect_count"] * _MINUTES_PER_CLUSTER_TRIAGE
            + bucket["defect_count"] * _MINUTES_PER_DUPLICATE_AVOIDED // 3
        )
        # Denormalized pass rate for the UI tile.
        total = bucket["test_count"]
        if total > 0:
            bucket["pass_rate"] = round(bucket["passed_count"] / total * 100, 1)
        else:
            bucket["pass_rate"] = 0.0

    # Stable sort — largest footprint first so the UI's top rows are
    # the teams with the most tests.
    teams_sorted = sorted(
        team_buckets.values(),
        key=lambda b: (-b["test_count"], b["team"]),
    )

    return {
        "project_id": project_id,
        "period_days": days,
        "team_count": len(teams_sorted),
        "teams": teams_sorted,
    }
