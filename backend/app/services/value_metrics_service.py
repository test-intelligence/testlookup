"""
Value Metrics Service — quantifies operational value delivered by the platform.

Two layers live here:

1. The original operational counters (defects auto-grouped, duplicate
   tickets avoided, flaky tests identified, releases blocked, …) kept for
   existing consumers of ``GET /api/v1/value-metrics``.
2. The PMF US-12.1 **engineer-hours-saved model**: three legs computed
   per month with per-project tunable assumptions —

   * ``hours_triage``     = clustered_failures × triage_minutes_per_failure / 60
   * ``hours_quarantine`` = runs_unblocked_proxy × blocked_run_wait_minutes / 60
   * ``hours_dedup``      = duplicates_absorbed × defect_filing_minutes / 60

   Assumptions default from published research (~3 h per non-trivial
   failure investigation; 15–25 min refocus/context-switch cost — the
   20-minute triage default sits inside that band) and are editable per
   project via ``/projects/{id}/value-metrics/assumptions`` (row in
   ``value_metric_assumptions``; a missing row resolves to code defaults).

   Honest-labeling caveats (also served by ``get_methodology()``):
   clusters are per-run, so sums are **cluster-instances**, not distinct
   defects; ``runs_unblocked_proxy`` is a proxy (runs where every failing
   case was under an active quarantine window), because no persisted
   "run unblocked" verdict exists.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func as sa_func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    Defect,
    DefectCandidate,
    FailureCluster,
    FlakyCoachResult,
    Project,
    ReleaseDecision,
    RunIntelligenceSnapshot,
    TestRun,
    ValueMetricAssumptions,
)

logger = logging.getLogger("services.value_metrics")

# ── US-12.1 model constants ─────────────────────────────────────────────────

METHODOLOGY_VERSION = 1

# Engineer-month divisor for the FTE headline (fte = hours / 173.2).
FTE_HOURS_PER_MONTH = 173.2

# Defaults from published research anchors: a non-trivial test-failure
# investigation costs ~3 engineer-hours end-to-end; an interruption costs
# 15–25 minutes of refocus/context-switch time. The 20-minute triage
# default sits inside that refocus band (deliberately conservative vs the
# ~3 h full-investigation anchor).
DEFAULT_TRIAGE_MINUTES_PER_FAILURE = 20.0
DEFAULT_BLOCKED_RUN_WAIT_MINUTES = 30.0
DEFAULT_DEFECT_FILING_MINUTES = 15.0

# Availability gate (US-12.2): the headline stays hidden until the project
# has at least this many days between first and last ingested run AND at
# least one nonzero leg in the last 30 days.
MIN_HISTORY_DAYS = 14
HEADLINE_WINDOW_DAYS = 30

# Quarantine states in which a quarantine is CURRENTLY EFFECTIVE — must
# mirror ``flaky_quarantine_service._ACTIVE_QUARANTINE_STATES`` (guarded by
# a parity test). Rows later RELEASED stop contributing, which undercounts
# history — conservative on purpose.
ACTIVE_QUARANTINE_STATES: tuple[str, ...] = (
    "QUARANTINED",
    "RECHECK_SCHEDULED",
    "RE_QUARANTINED",
)
_ACTIVE_STATES_SQL = "(" + ", ".join(f"'{s}'" for s in ACTIVE_QUARANTINE_STATES) + ")"
_FAILING_STATUSES_SQL = "('FAILED', 'BROKEN')"

_LEG_COUNT_KEYS = (
    "auto_triaged",
    "clustered_failures",
    "duplicates_absorbed",
    "quarantine_suppressed_failures",
    "runs_unblocked_proxy",
)


@dataclass(frozen=True)
class EffectiveAssumptions:
    """Resolved per-project hours-saved assumptions (missing row → defaults)."""
    triage_minutes_per_failure: float = DEFAULT_TRIAGE_MINUTES_PER_FAILURE
    blocked_run_wait_minutes: float = DEFAULT_BLOCKED_RUN_WAIT_MINUTES
    defect_filing_minutes: float = DEFAULT_DEFECT_FILING_MINUTES
    source: str = "default"  # "default" | "custom"

    def as_dict(self) -> dict:
        return {
            "triage_minutes_per_failure": self.triage_minutes_per_failure,
            "blocked_run_wait_minutes": self.blocked_run_wait_minutes,
            "defect_filing_minutes": self.defect_filing_minutes,
        }

    def fingerprint(self) -> str:
        """Cache-key component so a PUT invalidates cached computations."""
        return (
            f"{self.triage_minutes_per_failure}-"
            f"{self.blocked_run_wait_minutes}-{self.defect_filing_minutes}"
        )


# ── Assumptions resolution + upsert ─────────────────────────────────────────


async def get_assumptions_row(
    db: AsyncSession, project_id: uuid.UUID,
) -> Optional[ValueMetricAssumptions]:
    """The explicit assumptions row for a project, or ``None`` (= code
    defaults apply). Read-only."""
    result = await db.execute(
        select(ValueMetricAssumptions).where(
            ValueMetricAssumptions.project_id == project_id
        )
    )
    return result.scalar_one_or_none()


async def get_effective_assumptions(
    db: AsyncSession, project_id: Optional[uuid.UUID],
) -> EffectiveAssumptions:
    """Load the project's assumptions, resolving a missing row (or a
    missing project scope) to the defaults. Read-only."""
    if project_id is None:
        return EffectiveAssumptions()
    row = await get_assumptions_row(db, project_id)
    if row is None:
        return EffectiveAssumptions()
    return EffectiveAssumptions(
        triage_minutes_per_failure=float(
            row.triage_minutes_per_failure or DEFAULT_TRIAGE_MINUTES_PER_FAILURE
        ),
        blocked_run_wait_minutes=float(
            row.blocked_run_wait_minutes or DEFAULT_BLOCKED_RUN_WAIT_MINUTES
        ),
        defect_filing_minutes=float(
            row.defect_filing_minutes or DEFAULT_DEFECT_FILING_MINUTES
        ),
        source="custom",
    )


async def upsert_assumptions(
    db: AsyncSession,
    project_id: uuid.UUID,
    actor_id: Optional[uuid.UUID] = None,
    triage_minutes_per_failure: Optional[float] = None,
    blocked_run_wait_minutes: Optional[float] = None,
    defect_filing_minutes: Optional[float] = None,
) -> ValueMetricAssumptions:
    """Create or update the project's assumptions row.

    Stage-only: ``db.add`` + ``db.flush`` — the router handler owns the
    commit (transaction-boundary ratchet). ``None`` fields keep their
    current (or default) value.
    """
    row = await get_assumptions_row(db, project_id)
    if row is None:
        row = ValueMetricAssumptions(
            project_id=project_id,
            triage_minutes_per_failure=(
                triage_minutes_per_failure
                if triage_minutes_per_failure is not None
                else DEFAULT_TRIAGE_MINUTES_PER_FAILURE
            ),
            blocked_run_wait_minutes=(
                blocked_run_wait_minutes
                if blocked_run_wait_minutes is not None
                else DEFAULT_BLOCKED_RUN_WAIT_MINUTES
            ),
            defect_filing_minutes=(
                defect_filing_minutes
                if defect_filing_minutes is not None
                else DEFAULT_DEFECT_FILING_MINUTES
            ),
            updated_by_user_id=actor_id,
        )
        db.add(row)
    else:
        if triage_minutes_per_failure is not None:
            row.triage_minutes_per_failure = triage_minutes_per_failure
        if blocked_run_wait_minutes is not None:
            row.blocked_run_wait_minutes = blocked_run_wait_minutes
        if defect_filing_minutes is not None:
            row.defect_filing_minutes = defect_filing_minutes
        row.updated_by_user_id = actor_id
    await db.flush()
    # onupdate/server_default columns are expired after flush; refresh so a
    # later attribute access can't raise MissingGreenlet on the async session.
    await db.refresh(row)
    return row


# ── Leg count queries (raw SQL, month-bucketed or windowed) ─────────────────
#
# All four legs are project-scoped via joins (failure_clusters and
# ai_analysis have no project_id column). The quarantine join goes through
# indexed columns only (ix_fqr_fingerprint, ix_test_cases_fingerprint) —
# never through the unindexed JSON ``test_cases.tags``.

_TOTAL_KEY = "_total"


def _bucket_parts(monthly: bool, ts_expr: str) -> tuple[str, str]:
    """(select-prefix, group/order-suffix) for optional month bucketing."""
    if monthly:
        return (
            f"DATE_TRUNC('month', {ts_expr}) AS bucket,",
            "GROUP BY bucket ORDER BY bucket ASC",
        )
    return "", ""


def _rows_to_buckets(rows, value_names: list[str], monthly: bool) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        # Wire format is ISO "YYYY-MM-01" — every monthly consumer keys its
        # lookup with ISO strings (same load-bearing convention as
        # metrics_service daily buckets).
        key = row.bucket.strftime("%Y-%m-%d") if monthly else _TOTAL_KEY
        out[key] = {name: int(getattr(row, name) or 0) for name in value_names}
    return out


async def _cluster_counts(
    db: AsyncSession, project_id: uuid.UUID, since: datetime, monthly: bool,
) -> dict[str, dict]:
    """clustered_failures = SUM(size) and duplicates_absorbed = SUM(size-1)
    over clusters with size >= 2. Clusters are per-run → these are
    cluster-instances, NOT distinct defects."""
    bucket_sel, bucket_grp = _bucket_parts(monthly, "fc.created_at")
    query = text(f"""
        SELECT
            {bucket_sel}
            COALESCE(SUM(fc.size), 0)     AS clustered_failures,
            COALESCE(SUM(fc.size - 1), 0) AS duplicates_absorbed
        FROM failure_clusters fc
        JOIN test_runs tr ON tr.id = fc.test_run_id
        WHERE tr.project_id = :project_id
          AND fc.created_at >= :since
          AND fc.size >= 2
        {bucket_grp}
    """)
    result = await db.execute(query, {"project_id": str(project_id), "since": since})
    return _rows_to_buckets(
        result.fetchall(), ["clustered_failures", "duplicates_absorbed"], monthly
    )


async def _triage_counts(
    db: AsyncSession, project_id: uuid.UUID, since: datetime, monthly: bool,
) -> dict[str, dict]:
    """auto_triaged = AI analyses persisted in the window (context column;
    the triage leg itself multiplies clustered failures per the documented
    model)."""
    bucket_sel, bucket_grp = _bucket_parts(monthly, "aa.created_at")
    query = text(f"""
        SELECT
            {bucket_sel}
            COUNT(aa.id) AS auto_triaged
        FROM ai_analysis aa
        JOIN test_cases tc ON tc.id = aa.test_case_id
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE tr.project_id = :project_id
          AND aa.created_at >= :since
        {bucket_grp}
    """)
    result = await db.execute(query, {"project_id": str(project_id), "since": since})
    return _rows_to_buckets(result.fetchall(), ["auto_triaged"], monthly)


async def _quarantine_suppressed_counts(
    db: AsyncSession, project_id: uuid.UUID, since: datetime, monthly: bool,
) -> dict[str, dict]:
    """Failing executions whose fingerprint was inside an active quarantine
    window at run time (both join sides indexed)."""
    bucket_sel, bucket_grp = _bucket_parts(monthly, "tr.created_at")
    query = text(f"""
        SELECT
            {bucket_sel}
            COUNT(tc.id) AS quarantine_suppressed_failures
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE tr.project_id = :project_id
          AND tr.created_at >= :since
          AND tc.status IN {_FAILING_STATUSES_SQL}
          AND EXISTS (
              SELECT 1 FROM flaky_quarantine_requests fqr
              WHERE fqr.project_id = tr.project_id
                AND fqr.test_fingerprint = tc.test_fingerprint
                AND fqr.status IN {_ACTIVE_STATES_SQL}
                AND fqr.quarantine_start IS NOT NULL
                AND tr.created_at >= fqr.quarantine_start
                AND (fqr.quarantine_expires_at IS NULL
                     OR tr.created_at <= fqr.quarantine_expires_at)
          )
        {bucket_grp}
    """)
    result = await db.execute(query, {"project_id": str(project_id), "since": since})
    return _rows_to_buckets(
        result.fetchall(), ["quarantine_suppressed_failures"], monthly
    )


async def _runs_unblocked_counts(
    db: AsyncSession, project_id: uuid.UUID, since: datetime, monthly: bool,
) -> dict[str, dict]:
    """runs_unblocked_proxy = runs with >= 1 failing case where EVERY
    failing case matched an active quarantine window. A proxy: no persisted
    "run unblocked" verdict exists, so this approximates "quarantine was
    the difference between red and green"."""
    bucket_sel, bucket_grp = _bucket_parts(monthly, "tr.created_at")
    query = text(f"""
        SELECT
            {bucket_sel}
            COUNT(tr.id) AS runs_unblocked_proxy
        FROM test_runs tr
        WHERE tr.project_id = :project_id
          AND tr.created_at >= :since
          AND EXISTS (
              SELECT 1 FROM test_cases f
              WHERE f.test_run_id = tr.id
                AND f.status IN {_FAILING_STATUSES_SQL}
          )
          AND NOT EXISTS (
              SELECT 1 FROM test_cases nf
              WHERE nf.test_run_id = tr.id
                AND nf.status IN {_FAILING_STATUSES_SQL}
                AND NOT EXISTS (
                    SELECT 1 FROM flaky_quarantine_requests fqr
                    WHERE fqr.project_id = tr.project_id
                      AND fqr.test_fingerprint = nf.test_fingerprint
                      AND fqr.status IN {_ACTIVE_STATES_SQL}
                      AND fqr.quarantine_start IS NOT NULL
                      AND tr.created_at >= fqr.quarantine_start
                      AND (fqr.quarantine_expires_at IS NULL
                           OR tr.created_at <= fqr.quarantine_expires_at)
                )
          )
        {bucket_grp}
    """)
    result = await db.execute(query, {"project_id": str(project_id), "since": since})
    return _rows_to_buckets(result.fetchall(), ["runs_unblocked_proxy"], monthly)


async def _gather_leg_counts(
    db: AsyncSession, project_id: uuid.UUID, since: datetime, monthly: bool,
) -> dict[str, dict]:
    """Merge the four leg queries into ``{bucket: {count_key: int}}`` with
    every count key present (zero-filled)."""
    merged: dict[str, dict] = {}
    for partial in (
        await _cluster_counts(db, project_id, since, monthly),
        await _triage_counts(db, project_id, since, monthly),
        await _quarantine_suppressed_counts(db, project_id, since, monthly),
        await _runs_unblocked_counts(db, project_id, since, monthly),
    ):
        for bucket, counts in partial.items():
            merged.setdefault(bucket, {k: 0 for k in _LEG_COUNT_KEYS}).update(counts)
    return merged


# ── Pure model math (unit-testable without a DB) ────────────────────────────


def compute_leg_hours(counts: dict, assumptions: EffectiveAssumptions) -> dict:
    """The three leg formulas, from raw counts × assumptions (minutes → hours)."""
    hours_triage = (
        int(counts.get("clustered_failures") or 0)
        * assumptions.triage_minutes_per_failure / 60.0
    )
    hours_quarantine = (
        int(counts.get("runs_unblocked_proxy") or 0)
        * assumptions.blocked_run_wait_minutes / 60.0
    )
    hours_dedup = (
        int(counts.get("duplicates_absorbed") or 0)
        * assumptions.defect_filing_minutes / 60.0
    )
    return {
        "hours_triage": round(hours_triage, 2),
        "hours_quarantine": round(hours_quarantine, 2),
        "hours_dedup": round(hours_dedup, 2),
        "hours_total": round(hours_triage + hours_quarantine + hours_dedup, 2),
    }


def resolve_availability(
    first_run_at: Optional[datetime],
    last_run_at: Optional[datetime],
    hours_total_30d: float,
) -> tuple[bool, Optional[str]]:
    """US-12.2 gate: hide the headline until there's enough data to be
    honest about. Returns ``(available, insufficient_data_reason)``."""
    if first_run_at is None or last_run_at is None:
        return False, "no runs ingested yet"
    if (last_run_at - first_run_at) < timedelta(days=MIN_HISTORY_DAYS):
        return False, f"fewer than {MIN_HISTORY_DAYS} days of ingested runs"
    if hours_total_30d <= 0:
        return False, f"no automation signal in the last {HEADLINE_WINDOW_DAYS} days"
    return True, None


def _month_floor(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _months_back(dt: datetime, n: int) -> datetime:
    """First instant of the month ``n`` months before ``dt``'s month."""
    floor = _month_floor(dt)
    year = floor.year
    month = floor.month - n
    while month < 1:
        month += 12
        year -= 1
    return floor.replace(year=year, month=month)


def assemble_monthly_rows(
    counts_by_month: dict[str, dict],
    assumptions: EffectiveAssumptions,
    now: datetime,
) -> list[dict]:
    """Monthly trend rows: ascending, only months with any data plus the
    current month; ``month`` is the ISO "YYYY-MM-01" wire format."""
    current_key = _month_floor(now).strftime("%Y-%m-%d")
    keys = sorted(set(counts_by_month) | {current_key})
    rows: list[dict] = []
    for key in keys:
        counts = {
            k: int((counts_by_month.get(key) or {}).get(k) or 0)
            for k in _LEG_COUNT_KEYS
        }
        if key != current_key and not any(counts.values()):
            continue
        rows.append({"month": key, **counts, **compute_leg_hours(counts, assumptions)})
    return rows


# ── The hours-saved model (US-12.1) ─────────────────────────────────────────


async def _run_span(
    db: AsyncSession, project_id: uuid.UUID,
) -> tuple[Optional[datetime], Optional[datetime]]:
    result = await db.execute(
        select(
            sa_func.min(TestRun.created_at), sa_func.max(TestRun.created_at)
        ).where(TestRun.project_id == project_id)
    )
    row = result.first()
    return (row[0], row[1]) if row else (None, None)


def _empty_model(reason: str, assumptions: EffectiveAssumptions) -> dict:
    return {
        "available": False,
        "insufficient_data_reason": reason,
        "headline": {"hours_saved_30d": 0.0, "fte_equivalent_30d": 0.0},
        "monthly": [],
        "assumptions": assumptions.as_dict(),
        "assumptions_source": assumptions.source,
        "methodology_version": METHODOLOGY_VERSION,
    }


async def get_hours_saved_model(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    months: int = 6,
) -> dict:
    """The engineer-hours-saved model for a project: availability gate,
    last-30-day headline, monthly trend, effective assumptions.

    Cached in Redis for ~10 minutes; the assumptions fingerprint is part of
    the cache key so a PUT takes effect on the next read.
    """
    from app.services.cache_service import cache_get, cache_set, get_analytics_epoch

    assumptions = await get_effective_assumptions(db, project_id)
    if project_id is None:
        # The model is deliberately project-scoped (assumptions are per
        # project); an all-projects aggregate would mix rate cards.
        return _empty_model("select a project to compute the hours-saved model", assumptions)

    months = max(1, min(int(months), 24))
    cache_kwargs = {"months": months, "a": assumptions.fingerprint(), "v": METHODOLOGY_VERSION}
    # Read ONCE, before the query, and reuse for cache_set (VIZ-212).
    epoch = await get_analytics_epoch(project_id)
    cached = await cache_get("value_metrics_hours", str(project_id), epoch=epoch, **cache_kwargs)
    if cached is not None:
        return cached

    now = datetime.now(timezone.utc)
    first_run_at, last_run_at = await _run_span(db, project_id)

    headline_counts: dict = {k: 0 for k in _LEG_COUNT_KEYS}
    monthly_rows: list[dict] = []
    if first_run_at is not None:
        window_start = now - timedelta(days=HEADLINE_WINDOW_DAYS)
        gathered = await _gather_leg_counts(db, project_id, window_start, monthly=False)
        headline_counts.update(gathered.get(_TOTAL_KEY) or {})

        monthly_since = _months_back(now, months - 1)
        counts_by_month = await _gather_leg_counts(db, project_id, monthly_since, monthly=True)
        monthly_rows = assemble_monthly_rows(counts_by_month, assumptions, now)

    headline_hours = compute_leg_hours(headline_counts, assumptions)
    available, reason = resolve_availability(
        first_run_at, last_run_at, headline_hours["hours_total"]
    )

    hours_30d = headline_hours["hours_total"] if available else 0.0
    model = {
        "available": available,
        "insufficient_data_reason": reason,
        "headline": {
            "hours_saved_30d": hours_30d,
            "fte_equivalent_30d": round(hours_30d / FTE_HOURS_PER_MONTH, 3) if available else 0.0,
        },
        "monthly": monthly_rows,
        "assumptions": assumptions.as_dict(),
        "assumptions_source": assumptions.source,
        "methodology_version": METHODOLOGY_VERSION,
    }
    await cache_set(
        "value_metrics_hours", model, str(project_id), ttl=600, epoch=epoch, **cache_kwargs
    )
    return model


async def compute_headline(db: AsyncSession, project_id: uuid.UUID) -> dict:
    """Digest hook (US-12.2): the last-30-day headline plus the gate.

    Returns ``{"available": bool, "hours_saved_30d": float}`` — reuses the
    (cached) full model so the digest and the page never disagree.
    """
    model = await get_hours_saved_model(db, project_id, months=1)
    return {
        "available": bool(model.get("available")),
        "hours_saved_30d": float(
            ((model.get("headline") or {}).get("hours_saved_30d")) or 0.0
        ),
    }


def get_methodology() -> dict:
    """US-12.1 AC: the model documented — static JSON for the methodology
    page linked from the headline number."""
    defaults = EffectiveAssumptions()
    return {
        "version": METHODOLOGY_VERSION,
        "legs": [
            {
                "key": "triage",
                "title": "Failure triage avoided by auto-clustering",
                "formula": "hours_triage = clustered_failures × triage_minutes_per_failure / 60",
                "inputs": [
                    "clustered_failures — failing executions absorbed into failure clusters "
                    "of size ≥ 2 during the month (SUM of cluster sizes)",
                    "triage_minutes_per_failure — tunable per project "
                    f"(default {defaults.triage_minutes_per_failure})",
                ],
                "caveats": [
                    "Clusters are computed per run, so monthly sums are cluster-instances, "
                    "NOT distinct defects — the same underlying defect recurring across runs "
                    "counts each time it would have interrupted an engineer.",
                    "auto_triaged (AI analyses persisted) is reported alongside for context "
                    "but is not multiplied into the leg, to avoid double-counting failures "
                    "that were both clustered and AI-analyzed.",
                ],
            },
            {
                "key": "quarantine",
                "title": "CI wait time avoided by quarantine",
                "formula": "hours_quarantine = runs_unblocked_proxy × blocked_run_wait_minutes / 60",
                "inputs": [
                    "runs_unblocked_proxy — runs with ≥ 1 failing case where EVERY failing "
                    "case was under an active quarantine window at run time",
                    "blocked_run_wait_minutes — tunable per project "
                    f"(default {defaults.blocked_run_wait_minutes})",
                ],
                "caveats": [
                    "This is a PROXY: no persisted per-run 'unblocked by quarantine' verdict "
                    "exists, so the model approximates it as runs whose only failures were "
                    "quarantine-suppressed.",
                    "Quarantines that were later released stop contributing their historical "
                    "windows — a deliberate undercount.",
                ],
            },
            {
                "key": "dedup",
                "title": "Duplicate filing avoided by clustering",
                "formula": "hours_dedup = duplicates_absorbed × defect_filing_minutes / 60",
                "inputs": [
                    "duplicates_absorbed — SUM(size − 1) over clusters of size ≥ 2: the "
                    "duplicate FAILURES absorbed into an existing investigation",
                    "defect_filing_minutes — tunable per project "
                    f"(default {defaults.defect_filing_minutes})",
                ],
                "caveats": [
                    "Labeled 'duplicate failures absorbed', never 'defects deduped' — "
                    "clusters are per-run instances, not a cross-run defect ledger.",
                ],
            },
        ],
        "defaults": defaults.as_dict(),
        "research_notes": [
            "Published research puts a non-trivial test-failure investigation at "
            "roughly 3 engineer-hours end-to-end; the per-failure triage default is "
            "deliberately far below that anchor.",
            "Interruption studies put the refocus/context-switch cost at 15–25 minutes; "
            "the triage_minutes_per_failure default of 20 sits inside that band.",
            f"FTE equivalence uses {FTE_HOURS_PER_MONTH} engineer-hours per month.",
            "The headline is hidden until the project has at least "
            f"{MIN_HISTORY_DAYS} days between first and last ingested run and at "
            f"least one nonzero leg in the last {HEADLINE_WINDOW_DAYS} days.",
        ],
    }


# ── Legacy operational counters (pre-US-12.1 consumers) ─────────────────────


def _live_project_ids():
    """Subquery of the projects that still exist.

    ``DELETE /projects/{id}`` is a soft delete, so a deleted project's rows
    stay queryable. Every count below was guarded by ``if project_id:`` alone,
    which is the shape that made the dashboard over-count (#538) and the
    analytics helper leak (#539): unscoped, nothing restricted them.

    Measured on the live deployment, ``flaky_tests_identified`` returned 3
    unscoped — 2 from the live project and 1 from a soft-deleted one. The ROI
    page renders that as "FLAKY TESTS FOUND 3". An ROI figure gets quoted, so
    counting projects nobody can open overstates the product's own value.
    """
    return select(Project.id).where(Project.is_active.is_(True))


def _scope_via_run(stmt, on_clause, project_id):
    """Restrict a run-linked count to live projects, pinning one when given.

    The join is now unconditional. It was previously applied only on the
    scoped path, which is precisely why the unscoped path counted everything;
    joining on a foreign key to a single row does not multiply the count.
    """
    stmt = stmt.join(TestRun, on_clause).where(
        TestRun.project_id.in_(_live_project_ids())
    )
    if project_id:
        stmt = stmt.where(TestRun.project_id == project_id)
    return stmt


def _scope_direct(stmt, column, project_id):
    """Same, for tables carrying ``project_id`` themselves."""
    stmt = stmt.where(column.in_(_live_project_ids()))
    if project_id:
        stmt = stmt.where(column == project_id)
    return stmt


async def get_value_metrics(
    db: AsyncSession,
    project_id: Optional[uuid.UUID] = None,
    days: int = 30,
    months: int = 6,
) -> dict:
    """
    Compute operational value metrics for a project (or all projects) over a
    time window, merged with the US-12.1 engineer-hours-saved model.

    Existing keys are preserved for old consumers; their minute rates now
    come from the project's effective assumptions instead of hardcoded
    constants.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    assumptions = await get_effective_assumptions(db, project_id)

    # ── Defects auto-grouped (clusters with ≥ 2 members) ────────────────────
    cluster_stmt = select(sa_func.count(FailureCluster.id)).where(
        FailureCluster.created_at >= cutoff,
        FailureCluster.size >= 2,
    )
    cluster_stmt = _scope_via_run(
        cluster_stmt, FailureCluster.test_run_id == TestRun.id, project_id
    )
    defects_grouped = (await db.execute(cluster_stmt)).scalar() or 0

    # Total tests grouped (sum of member_count across clusters)
    tests_grouped_stmt = select(sa_func.coalesce(sa_func.sum(FailureCluster.size), 0)).where(
        FailureCluster.created_at >= cutoff,
        FailureCluster.size >= 2,
    )
    tests_grouped_stmt = _scope_via_run(
        tests_grouped_stmt, FailureCluster.test_run_id == TestRun.id, project_id
    )
    tests_grouped = (await db.execute(tests_grouped_stmt)).scalar() or 0

    # ── Duplicate tickets avoided ────────────────────────────────────────────
    dup_stmt = select(sa_func.count(Defect.id)).where(
        Defect.created_at >= cutoff,
        Defect.is_duplicate.is_(True),
    )
    dup_stmt = _scope_direct(dup_stmt, Defect.project_id, project_id)
    duplicates_avoided = (await db.execute(dup_stmt)).scalar() or 0

    # Also count candidates flagged as duplicate (not yet promoted)
    dup_cand_stmt = select(sa_func.count(DefectCandidate.id)).where(
        DefectCandidate.created_at >= cutoff,
        DefectCandidate.is_duplicate.is_(True),
    )
    dup_cand_stmt = _scope_via_run(
        dup_cand_stmt, DefectCandidate.run_id == TestRun.id, project_id
    )
    dup_candidates = (await db.execute(dup_cand_stmt)).scalar() or 0
    total_duplicates_avoided = duplicates_avoided + dup_candidates

    # ── Defects promoted ─────────────────────────────────────────────────────
    promoted_stmt = select(sa_func.count(Defect.id)).where(
        Defect.created_at >= cutoff,
        Defect.promotion_source == "cluster_promotion",
    )
    promoted_stmt = _scope_direct(promoted_stmt, Defect.project_id, project_id)
    defects_promoted = (await db.execute(promoted_stmt)).scalar() or 0

    # ── Flaky tests identified ───────────────────────────────────────────────
    # Counts rows that actually OSCILLATE, not every row in the coach table.
    #
    # This used to be a bare COUNT(*), so it reported 5 where the coach headline
    # reported 1 for the same project at the same moment — and unlike a UI
    # disagreement, this number is an ROI figure that gets quoted. The coach
    # table deliberately KEEPS persistent regressions (they carry a downgraded
    # recommendation and "treat as a regression" advice), so the row count and
    # the flaky count are legitimately different things.
    #
    # ``status_history`` is a stored column, so this needs the rows rather than
    # a COUNT — bounded by the number of flagged tests in one project, and the
    # coach page already loads the same set. Shares the coach's predicate so a
    # third definition cannot appear.
    from app.services.test_health_coach_service import history_is_intermittent

    flaky_rows_stmt = select(FlakyCoachResult.status_history)
    flaky_rows_stmt = _scope_direct(
        flaky_rows_stmt, FlakyCoachResult.project_id, project_id
    )
    flaky_identified = sum(
        1
        for (history,) in (await db.execute(flaky_rows_stmt)).all()
        if history_is_intermittent(history)
    )

    quarantine_stmt = select(sa_func.count(FlakyCoachResult.id)).where(
        FlakyCoachResult.quarantine_recommendation == "QUARANTINE",
    )
    quarantine_stmt = _scope_direct(
        quarantine_stmt, FlakyCoachResult.project_id, project_id
    )
    quarantine_recommended = (await db.execute(quarantine_stmt)).scalar() or 0

    # ── Risky releases blocked ───────────────────────────────────────────────
    blocked_stmt = select(sa_func.count(ReleaseDecision.id)).where(
        ReleaseDecision.created_at >= cutoff,
        ReleaseDecision.recommendation == "NO_GO",
    )
    blocked_stmt = _scope_via_run(
        blocked_stmt, ReleaseDecision.test_run_id == TestRun.id, project_id
    )
    releases_blocked = (await db.execute(blocked_stmt)).scalar() or 0

    conditional_stmt = select(sa_func.count(ReleaseDecision.id)).where(
        ReleaseDecision.created_at >= cutoff,
        ReleaseDecision.recommendation == "CONDITIONAL_GO",
    )
    conditional_stmt = _scope_via_run(
        conditional_stmt, ReleaseDecision.test_run_id == TestRun.id, project_id
    )
    releases_conditional = (await db.execute(conditional_stmt)).scalar() or 0

    overrides_stmt = select(sa_func.count(ReleaseDecision.id)).where(
        ReleaseDecision.created_at >= cutoff,
        ReleaseDecision.human_override.isnot(None),
    )
    overrides_stmt = _scope_via_run(
        overrides_stmt, ReleaseDecision.test_run_id == TestRun.id, project_id
    )
    release_overrides = (await db.execute(overrides_stmt)).scalar() or 0

    # ── Intelligence reports ─────────────────────────────────────────────────
    intel_stmt = select(sa_func.count(RunIntelligenceSnapshot.id)).where(
        RunIntelligenceSnapshot.created_at >= cutoff,
    )
    intel_stmt = _scope_via_run(
        intel_stmt, RunIntelligenceSnapshot.run_id == TestRun.id, project_id
    )
    intelligence_reports = (await db.execute(intel_stmt)).scalar() or 0

    # ── Triage time saved estimate (legacy scalar — assumptions-based) ──────
    # Rate mapping: cluster triage + intelligence report → per-failure triage
    # minutes; duplicate ticket avoided → defect filing minutes.
    triage_minutes_saved = round(
        defects_grouped * assumptions.triage_minutes_per_failure
        + total_duplicates_avoided * assumptions.defect_filing_minutes
        + intelligence_reports * assumptions.triage_minutes_per_failure,
        1,
    )

    legacy = {
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

    model = await get_hours_saved_model(db, project_id, months=months)
    return {**legacy, **model}
