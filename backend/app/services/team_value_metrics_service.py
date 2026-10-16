"""Value metrics by owning team (W08 TRND-08; was Tier 2 item 11).

``GET /api/v1/value-metrics/by-team`` answers "which team does the automation
save time for, and how are its tests doing" for one project and window.

**One owner rule.** A test's team is what
``ownership_resolver_service.resolve_test_ownership`` says for its attributes
-- the resolver ``/analytics/top-failing`` and the failure detail use
(``failure_detail_service._rule_owner_of``), over the same rules (active,
priority first) and the project's component map. A blank team, ``unknown``,
or no match is **Unassigned**. The old fnmatch-over-"suite name" matcher here
disagreed with every other surface and is gone.

**Every test counts.** One grouped statement per (effective suite, test) over
the run clock: no ``.limit``, so a project with 60 000 executions in the window
is 60 000, not the first 50 000. ``tests`` counts those rows; a test that ran
under two suites is two rows (each resolved on its own suite).

**Hours are the headline's own legs.** The window headline
(``value_metrics_service.compute_leg_hours``) is three legs: clustered failures
x triage minutes, duplicates absorbed x filing minutes, runs unblocked by
quarantine x blocked-run wait. The first two are attributed to teams through
each cluster's MEMBERS -- a cluster of size ``s`` with ``m`` members found
gives each member ``s / m`` clustered failures and ``(s - 1) / m`` absorbed
duplicates, so the per-team sums ARE the leg counts; a cluster with no member
found is all Unassigned. The third is per RUN, not per test, and is reported
as ``not_attributable_hours``. So the teams' hours plus that equal
``headline_hours``, which is computed here from the SAME legs over the SAME
``since`` (``now - days``, the rule ``get_hours_saved_model`` uses), uncached
and ungated -- the cached model could be ten minutes older than the teams.

**Null, never zero.** A team with nothing evaluated has ``pass_rate: None``; a
team with no resolved defect has ``mttr_hours: None``.

Pure: no writes, services never commit.
"""
from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Iterable, Mapping, Optional, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pass_rate import canonical_pass_rate
from app.models.postgres import Defect, Project, ServiceOwnershipRule, TestCase, TestRun
from app.services.analytics_scope import AnalyticsScope, effective_suite_expr, effective_suite_sql, scoped_text
from app.services.analytics_service import _tenant_filter as tenant_filter_sql
from app.services.cluster_snapshot import current_snapshot_sql

logger = structlog.get_logger(__name__)

UNASSIGNED: Final = "Unassigned"
NO_RULES_REASON: Final = "No ownership rules yet."
#: A member id that can be a ``test_cases.id``: a cluster's member list is
#: written by the pipeline, but one malformed entry must not fail the cast.
_UUID_PATTERN: Final = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


# ── Ownership ──────────────────────────────────────────────────────────────


def ownership_attrs(row: Mapping[str, Any]) -> dict[str, str]:
    """The resolver's attributes from one test row, mapped exactly as
    ``failure_detail_service._rule_owner_of`` maps them."""
    return {
        "suite_name": row.get("suite_name") or "",
        "component": row.get("class_name") or "",
        "package": row.get("package_name") or "",
        "path": row.get("full_name") or "",
        "label": row.get("own_label") or "",
        "owner": row.get("own_owner") or "",
    }


def team_of(rules: Sequence[Any], component_map: Optional[dict], row: Mapping[str, Any]) -> str:
    """The owning team of one test row, or :data:`UNASSIGNED`."""
    from app.services.ownership_resolver_service import resolve_test_ownership

    result = resolve_test_ownership(list(rules), ownership_attrs(row), component_map)
    team = str(result.team_name or "").strip()
    if not team or team.lower() == "unknown" or not result.match_source:
        return UNASSIGNED
    return team


# ── Pure attribution (unit-tested without a database) ──────────────────────


@dataclass
class TeamTally:
    """One team's figures while they are being summed."""

    team: str
    tests: int = 0
    executions: int = 0
    evaluated: int = 0
    passed: int = 0
    flaky_tests: int = 0
    defects: int = 0
    resolved_hours: list[float] = field(default_factory=list)
    clustered: float = 0.0
    absorbed: float = 0.0


def attribute_clusters(
    members: Iterable[tuple[Any, int, Optional[str]]],
) -> dict[str, tuple[float, float]]:
    """``team -> (clustered failures, absorbed duplicates)`` from cluster members.

    ``members`` is one ``(cluster_id, size, team)`` per member FOUND, or one
    ``(cluster_id, size, None)`` for a cluster none of whose members was found.
    A cluster of size ``s`` with ``m`` members found gives each ``s / m`` and
    ``(s - 1) / m``; a cluster with none found gives all of it to Unassigned.
    Summed over every team the two figures are ``SUM(size)`` and
    ``SUM(size - 1)``: the headline's own counts.
    """
    clusters: dict[Any, tuple[int, list[str]]] = {}
    for cluster_id, size, team in members:
        entry = clusters.setdefault(cluster_id, (int(size), []))
        if team is not None:
            entry[1].append(team)
    out: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for size, teams in clusters.values():
        if not teams:
            out[UNASSIGNED][0] += size
            out[UNASSIGNED][1] += size - 1
            continue
        share = 1 / len(teams)
        for team in teams:
            out[team][0] += size * share
            out[team][1] += (size - 1) * share
    return {team: (vals[0], vals[1]) for team, vals in out.items()}


def _team_row(tally: TeamTally, triage_minutes: float, filing_minutes: float) -> dict:
    triage = tally.clustered * triage_minutes / 60.0
    dedup = tally.absorbed * filing_minutes / 60.0
    failing = tally.evaluated - tally.passed
    return {
        "team": tally.team,
        "tests": tally.tests,
        "executions": tally.executions,
        "evaluated": tally.evaluated,
        "passed": tally.passed,
        "pass_rate": (
            canonical_pass_rate(tally.passed, failing, ndigits=1) if tally.evaluated > 0 else None
        ),
        "flaky_tests": tally.flaky_tests,
        "defects": tally.defects,
        "mttr_hours": (
            round(math.fsum(tally.resolved_hours) / len(tally.resolved_hours), 2)
            if tally.resolved_hours else None
        ),
        "hours_saved": round(triage + dedup, 2),
        "hours": {"triage": round(triage, 2), "dedup": round(dedup, 2)},
    }


def order_teams(rows: list[dict]) -> list[dict]:
    """``hours_saved`` desc, then ``tests`` desc, then the name; Unassigned last."""
    named = sorted(
        (row for row in rows if row["team"] != UNASSIGNED),
        key=lambda row: (-row["hours_saved"], -row["tests"], row["team"]),
    )
    return named + [row for row in rows if row["team"] == UNASSIGNED]


def assemble_teams(
    tallies: Mapping[str, TeamTally], *, triage_minutes: float, filing_minutes: float,
) -> list[dict]:
    """The response's ``teams``: a row per team with anything to show."""
    rows = [
        _team_row(tally, triage_minutes, filing_minutes)
        for tally in tallies.values()
        if tally.tests or tally.defects or tally.clustered or tally.absorbed
    ]
    return order_teams(rows)


# ── The reads ──────────────────────────────────────────────────────────────


async def _ownership_inputs(
    db: AsyncSession, project_id: uuid.UUID,
) -> tuple[list[Any], Optional[dict]]:
    """The project's active rules (priority first) and its component map,
    loaded exactly as ``failure_detail_service._rule_owner_of`` loads them."""
    rules = (
        await db.execute(
            select(ServiceOwnershipRule)
            .where(
                ServiceOwnershipRule.project_id == project_id,
                ServiceOwnershipRule.is_active.is_(True),
            )
            .order_by(ServiceOwnershipRule.priority.desc(), ServiceOwnershipRule.created_at)
        )
    ).scalars().all()
    component_map = (
        await db.execute(select(Project.component_owner_map).where(Project.id == project_id))
    ).scalar_one_or_none()
    return list(rules), component_map


async def _headline_legs(db: AsyncSession, project_id: uuid.UUID, since: datetime) -> dict:
    """The window headline's leg counts, from the headline's own queries."""
    from app.services.value_metrics_service import (
        _TOTAL_KEY,
        _cluster_counts,
        _runs_unblocked_counts,
    )

    clusters = (await _cluster_counts(db, project_id, since, monthly=False)).get(_TOTAL_KEY) or {}
    unblocked = (await _runs_unblocked_counts(db, project_id, since, monthly=False)).get(_TOTAL_KEY) or {}
    return {**clusters, **unblocked}


async def _test_rows(db: AsyncSession, project_id: uuid.UUID, since: datetime) -> list[dict]:
    """One row per (effective suite, test) executed in the window. Bounded by
    distinct tests, never by a row limit."""
    params: dict[str, Any] = {"since": since}
    tenant = tenant_filter_sql(params, project_id=str(project_id), allowed_project_ids=None)
    statement = scoped_text(
        f"""
        SELECT
            {effective_suite_sql()} AS suite_name,
            tc.test_fingerprint,
            MIN(tc.class_name) AS class_name,
            MIN(tc.package_name) AS package_name,
            MIN(tc.full_name) AS full_name,
            MIN(COALESCE(tc.feature, tc.epic)) AS own_label,
            MIN(tc.owner) AS own_owner,
            COUNT(*) AS executions,
            COUNT(*) FILTER (WHERE tc.status = 'PASSED') AS passed,
            COUNT(*) FILTER (WHERE tc.status IN ('PASSED', 'FAILED', 'BROKEN')) AS evaluated,
            BOOL_OR(tc.is_flaky_run IS TRUE) AS flaky
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE tr.created_at >= :since
          {tenant}
        GROUP BY 1, 2
        """,
        params,
    )
    return [dict(row._mapping) for row in (await db.execute(statement, params)).fetchall()]


async def _defect_rows(db: AsyncSession, project_id: uuid.UUID, since: datetime) -> list[dict]:
    """Defects created in the window, with their test row's ownership attributes."""
    result = await db.execute(
        select(
            Defect.id,
            Defect.created_at,
            Defect.resolved_at,
            TestCase.test_fingerprint,
            effective_suite_expr().label("suite_name"),
            TestCase.class_name,
            TestCase.package_name,
            TestCase.full_name,
            TestCase.feature,
            TestCase.epic,
            TestCase.owner,
        )
        .join(TestCase, TestCase.id == Defect.test_case_id)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(TestRun.project_id == project_id, Defect.created_at >= since)
    )
    rows = []
    for row in result.all():
        data = dict(row._mapping)
        feature, epic = data.pop("feature"), data.pop("epic")
        # ``COALESCE(tc.feature, tc.epic)``, as the grouped statement reads it.
        data["own_label"] = feature if feature is not None else epic
        data["own_owner"] = data.pop("owner")
        rows.append(data)
    return rows


async def _cluster_member_rows(db: AsyncSession, project_id: uuid.UUID, since: datetime) -> list[dict]:
    """Every member of the headline's clusters (``_cluster_counts``' set: in the
    window, ``size >= 2``, the current snapshot), with the member test row's
    ownership attributes; a cluster with no member found is one row of NULLs.
    ``member_test_ids`` is JSONB in the database (migration 0006)."""
    params: dict[str, Any] = {"project_id": str(project_id), "since": since, "uuid_re": _UUID_PATTERN}
    statement = scoped_text(
        f"""
        WITH clusters AS (
            SELECT fc.id AS cluster_id, fc.size AS size, fc.member_test_ids AS members
            FROM failure_clusters fc
            JOIN test_runs tr ON tr.id = fc.test_run_id
            WHERE tr.project_id = :project_id
              AND fc.created_at >= :since
              AND fc.size >= 2
              AND {current_snapshot_sql('fc')}
        ), member_ids AS (
            SELECT DISTINCT c.cluster_id, m.member_id
            FROM clusters c
            CROSS JOIN LATERAL jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(c.members::jsonb) = 'array'
                     THEN c.members::jsonb ELSE '[]'::jsonb END
            ) AS m(member_id)
        ), members AS (
            SELECT
                mi.cluster_id,
                tc.test_fingerprint,
                {effective_suite_sql()} AS suite_name,
                tc.class_name,
                tc.package_name,
                tc.full_name,
                COALESCE(tc.feature, tc.epic) AS own_label,
                tc.owner AS own_owner
            FROM member_ids mi
            JOIN test_cases tc ON tc.id = CASE
                WHEN lower(mi.member_id) ~ :uuid_re THEN CAST(mi.member_id AS uuid)
            END
            JOIN test_runs tr ON tr.id = tc.test_run_id
            WHERE tr.project_id = :project_id
        )
        SELECT c.cluster_id, c.size, m.test_fingerprint, m.suite_name, m.class_name,
               m.package_name, m.full_name, m.own_label, m.own_owner
        FROM clusters c
        LEFT JOIN members m ON m.cluster_id = c.cluster_id
        ORDER BY c.cluster_id, m.test_fingerprint
        """,
        params,
    )
    return [dict(row._mapping) for row in (await db.execute(statement, params)).fetchall()]


# ── The entry point ────────────────────────────────────────────────────────


async def get_team_value_metrics(db: AsyncSession, scope: AnalyticsScope) -> dict[str, Any]:
    """The by-team body (W08 contract §1.3) for the scope's one project."""
    from app.services.value_metrics_service import compute_leg_hours, get_effective_assumptions

    if scope.project_id is None:  # pragma: no cover - the route's policy requires one
        raise ValueError("value by team is per project")
    project_id = scope.project_id
    days = scope.window_days
    # The headline's own window rule (``get_hours_saved_model``): now - days.
    since = datetime.now(timezone.utc) - timedelta(days=days)

    assumptions = await get_effective_assumptions(db, project_id)
    legs = compute_leg_hours(await _headline_legs(db, project_id, since), assumptions)
    rules, component_map = await _ownership_inputs(db, project_id)
    if not rules and not component_map:
        return {
            "window_days": days,
            "measured": False,
            "reason": NO_RULES_REASON,
            "rules_configured": False,
            "teams": [],
            "not_attributable_hours": legs["hours_quarantine"],
            "headline_hours": legs["hours_total"],
        }

    tallies: dict[str, TeamTally] = {}

    def tally(team: str) -> TeamTally:
        return tallies.setdefault(team, TeamTally(team=team))

    # The fingerprint's team for defects and cluster members: the team of its
    # busiest row (most executions; a tie goes to the smaller suite name), so
    # a test that ran under two suites has one answer.
    team_of_fp: dict[str, tuple[int, str, str]] = {}
    for row in await _test_rows(db, project_id, since):
        team = team_of(rules, component_map, row)
        bucket = tally(team)
        executions = int(row["executions"] or 0)
        bucket.tests += 1
        bucket.executions += executions
        bucket.evaluated += int(row["evaluated"] or 0)
        bucket.passed += int(row["passed"] or 0)
        bucket.flaky_tests += 1 if row["flaky"] else 0
        fp, suite = row["test_fingerprint"], str(row["suite_name"] or "")
        best = team_of_fp.get(fp)
        if best is None or executions > best[0] or (executions == best[0] and suite < best[1]):
            team_of_fp[fp] = (executions, suite, team)

    def fp_team(row: Mapping[str, Any]) -> str:
        known = team_of_fp.get(row.get("test_fingerprint") or "")
        return known[2] if known is not None else team_of(rules, component_map, row)

    for defect in await _defect_rows(db, project_id, since):
        bucket = tally(fp_team(defect))
        bucket.defects += 1
        created, resolved = defect["created_at"], defect["resolved_at"]
        if created is not None and resolved is not None:
            hours = (resolved - created).total_seconds() / 3600.0
            if hours >= 0:
                bucket.resolved_hours.append(hours)

    members = [
        (row["cluster_id"], int(row["size"] or 0), fp_team(row) if row["test_fingerprint"] else None)
        for row in await _cluster_member_rows(db, project_id, since)
    ]
    for team, (clustered, absorbed) in attribute_clusters(members).items():
        bucket = tally(team)
        bucket.clustered += clustered
        bucket.absorbed += absorbed

    teams = assemble_teams(
        tallies,
        triage_minutes=assumptions.triage_minutes_per_failure,
        filing_minutes=assumptions.defect_filing_minutes,
    )
    logger.info("team_value_metrics_built", teams=len(teams), clusters=len({m[0] for m in members}))
    return {
        "window_days": days,
        "measured": True,
        "reason": None,
        "rules_configured": True,
        "teams": teams,
        "not_attributable_hours": legs["hours_quarantine"],
        "headline_hours": legs["hours_total"],
    }


__all__ = [
    "NO_RULES_REASON",
    "TeamTally",
    "UNASSIGNED",
    "assemble_teams",
    "attribute_clusters",
    "get_team_value_metrics",
    "order_teams",
    "ownership_attrs",
    "team_of",
]
