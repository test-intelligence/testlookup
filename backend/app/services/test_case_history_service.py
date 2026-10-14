"""Read-only test-case history / flakiness / metadata helper (Phase 2).

Surfaces the cross-run timeline, computed flakiness, and identity metadata for
a single logical test, identified by ``(run_id, test_id)``. **READ-ONLY** — no
``db.commit()``, no ingestion writes, no new formula. It reuses existing
machinery:

  * Timeline: the windowed ``ROW_NUMBER() PARTITION BY test_fingerprint`` pattern
    from ``test_health_coach_service.refresh_flaky_coach`` over
    ``test_case_history``, project-scoped via a JOIN to ``test_runs`` (the
    ``test_fingerprint`` is NOT project-salted, so every history query MUST scope
    by project to avoid cross-tenant leakage).
  * Flakiness: ``failure_rate`` matches ``analytics_service.flaky_tests``
    (``FAILED``/``BROKEN`` over total in the window); classification +
    ``impact_score`` reuse ``test_health_coach_service`` thresholds — no new
    formula is invented here.
  * Metadata: owner / suite from the per-run ``test_cases`` row (effective suite
    via ``_effective_suite_sql``); first/last seen + created/updated from
    ``canonical_test_cases``.

The router owns the (no-op) transaction; this module only reads.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func as sa_func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    CanonicalTestCase,
    TestCase,
    TestCaseHistory,
    TestRun,
)
from app.services.analytics_service import _effective_suite_sql
from app.services.test_health_coach_service import (
    _compute_impact_score,
    _compute_quarantine_recommendation,
)

# Hard window on the cross-run timeline so the payload stays bounded even for a
# test that has run thousands of times. Mirrors the "last N runs" intent of the
# flaky-coach top-30 slice; 50 is a sane detail-view depth.
_HISTORY_LIMIT = 50

# Flakiness aggregation window (days). Matches the flaky-coach default so the
# computed failure_rate lines up with what /flaky-coach + /failures show.
_FLAKY_WINDOW_DAYS = 30

_FAILED_STATUSES = ("FAILED", "BROKEN")


async def get_test_case_history(
    db: AsyncSession,
    run_id: uuid.UUID,
    test_id: uuid.UUID,
) -> dict | None:
    """Return ``{history, flakiness, metadata}`` for one test, or ``None``.

    ``None`` (→ 404 at the router) when the ``test_id`` doesn't belong to
    ``run_id``. The test's ``project_id`` is resolved from the run, and EVERY
    history/flakiness query is scoped to that project — a same-fingerprint test
    in another project never appears.
    """
    # Resolve the per-run test row + its run (for project scope + the
    # effective-suite expression). One row identifies the logical test.
    row = (
        await db.execute(
            select(TestCase, TestRun)
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(TestCase.id == test_id, TestCase.test_run_id == run_id)
        )
    ).first()
    if row is None:
        return None
    tc: TestCase = row[0]
    run: TestRun = row[1]
    project_id = run.project_id
    fingerprint = tc.test_fingerprint

    history = await _history_timeline(db, project_id, fingerprint)
    flakiness = await _flakiness_in_window(db, project_id, fingerprint)
    metadata = await _metadata(db, project_id, fingerprint, tc, run)
    duration_stats = await _duration_stats_in_window(db, project_id, fingerprint)
    baseline = await _baseline(db, project_id, fingerprint)

    return {
        "run_id": str(run_id),
        "test_id": str(test_id),
        "test_fingerprint": fingerprint,
        "test_name": tc.test_name,
        "history": history,
        "flakiness": flakiness,
        "metadata": metadata,
        "duration_stats": duration_stats,
        "baseline": baseline,
    }


async def _history_timeline(
    db: AsyncSession,
    project_id: uuid.UUID,
    fingerprint: str | None,
) -> list[dict]:
    """Top-``_HISTORY_LIMIT`` cross-run history points, most-recent-first.

    Reuses the ``ROW_NUMBER() PARTITION BY test_fingerprint ORDER BY created_at
    DESC`` window from ``refresh_flaky_coach``. The JOIN to ``test_runs`` enforces
    the project scope (fingerprint is not salted) and supplies the run label
    fields. ``run_seq`` carries the human-readable "Run #N" for the detail view.

    The ``created_at >= cutoff`` filter bounds the timeline to the same 30-day
    window the flakiness value honours, matching ``refresh_flaky_coach``
    (``created_at >= cutoff``) and ``analytics_service.flaky_tests``
    (``created_at >= :period_start``). ``_HISTORY_LIMIT`` is then a secondary
    display-depth bound on top of the window.
    """
    if not fingerprint:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=_FLAKY_WINDOW_DAYS)
    _rn = sa_func.row_number().over(
        partition_by=TestCaseHistory.test_fingerprint,
        order_by=TestCaseHistory.created_at.desc(),
    ).label("rn")
    ranked = (
        select(
            TestCaseHistory.test_run_id.label("run_id"),
            TestCaseHistory.test_case_id.label("test_case_id"),
            TestCaseHistory.status.label("status"),
            TestCaseHistory.duration_ms.label("duration_ms"),
            TestCaseHistory.created_at.label("created_at"),
            TestRun.build_number.label("build_number"),
            TestRun.primary_suite_name.label("primary_suite_name"),
            TestRun.trigger_source.label("trigger_source"),
            _rn,
        )
        .join(TestRun, TestRun.id == TestCaseHistory.test_run_id)
        .where(
            TestRun.project_id == project_id,
            TestCaseHistory.test_fingerprint == fingerprint,
            TestCaseHistory.created_at >= cutoff,
        )
        .subquery()
    )
    stmt = (
        select(ranked)
        .where(ranked.c.rn <= _HISTORY_LIMIT)
        .order_by(ranked.c.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()

    # Human-readable "Run #N" labels, scoped to the project per the shared
    # runs_service helper (per-(project, suite) sequence). Best-effort — a
    # missing seq just omits the field.
    run_ids = [r.run_id for r in rows if r.run_id]
    seq_map: dict[str, int] = {}
    if run_ids:
        try:
            from app.services.runs_service import fetch_run_seq_map
            seq_map = await fetch_run_seq_map(db, run_ids)
        except Exception:
            seq_map = {}

    timings = await _point_timings(db, [getattr(r, "test_case_id", None) for r in rows])

    timeline: list[dict] = []
    for r in rows:
        rid = str(r.run_id) if r.run_id else None
        seq = seq_map.get(rid) if rid else None
        label = (
            f"Run #{seq}" if seq is not None
            else (r.build_number or (rid[:8] if rid else "unknown"))
        )
        timeline.append({
            "run_id": rid,
            "run_label": label,
            "build_number": r.build_number,
            "run_seq": seq,
            "status": str(r.status),
            "duration_ms": r.duration_ms,
            "created_at": r.created_at,
            # EXEC-03: the row's execution timing; every key None when the
            # format carried none (never 0).
            **timings.get(getattr(r, "test_case_id", None), _NO_TIMING),
        })
    return timeline


#: The timing keys of a history point whose row carries none.
_NO_TIMING: dict = {
    "started_at": None,
    "finished_at": None,
    "setup_ms": None,
    "call_ms": None,
    "teardown_ms": None,
    "worker_id": None,
    "timing_source": None,
}


async def _point_timings(db: AsyncSession, test_case_ids: list) -> dict:
    """EXEC-03: the execution timing of each history point's own ``test_cases``
    row, by primary key -- at most ``_HISTORY_LIMIT`` ids, already project-
    scoped by the timeline query that produced them."""
    ids = [i for i in test_case_ids if i is not None]
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(
                TestCase.id,
                TestCase.started_at,
                TestCase.finished_at,
                TestCase.setup_ms,
                TestCase.call_ms,
                TestCase.teardown_ms,
                TestCase.worker_id,
                TestCase.timing_source,
            ).where(TestCase.id.in_(ids))
        )
    ).all()
    return {
        row.id: {
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "setup_ms": row.setup_ms,
            "call_ms": row.call_ms,
            "teardown_ms": row.teardown_ms,
            "worker_id": row.worker_id,
            "timing_source": row.timing_source,
        }
        for row in rows
    }


#: The executions a test's duration percentiles are taken over: the population
#: its ``perf_baselines`` row learns from (PASSED and FAILED, a positive
#: duration), so the window figures and the baseline compare like with like.
_DURATION_STATUSES = ("PASSED", "FAILED")


async def _duration_stats_in_window(
    db: AsyncSession,
    project_id: uuid.UUID,
    fingerprint: str | None,
) -> dict:
    """EXEC-03: p50 / p95 / max duration of the test over the 30-day history
    window -- every execution in it, not only the 50 points the timeline
    returns. Project-scoped through ``test_runs`` (fingerprints are not
    salted). ``measured: False`` with a reason when nothing carried one."""
    out: dict[str, Any] = {
        "window_days": _FLAKY_WINDOW_DAYS,
        "n": 0,
        "p50_ms": None,
        "p95_ms": None,
        "max_ms": None,
        "measured": False,
        "reason": "no execution in the window carried a duration",
    }
    if not fingerprint:
        return out
    cutoff = datetime.now(timezone.utc) - timedelta(days=_FLAKY_WINDOW_DAYS)
    row = (
        await db.execute(
            select(
                sa_func.count(TestCaseHistory.duration_ms).label("n"),
                sa_func.percentile_cont(0.5)
                .within_group(TestCaseHistory.duration_ms.asc())
                .label("p50"),
                sa_func.percentile_cont(0.95)
                .within_group(TestCaseHistory.duration_ms.asc())
                .label("p95"),
                sa_func.max(TestCaseHistory.duration_ms).label("max_ms"),
            )
            .join(TestRun, TestRun.id == TestCaseHistory.test_run_id)
            .where(
                TestRun.project_id == project_id,
                TestCaseHistory.test_fingerprint == fingerprint,
                TestCaseHistory.created_at >= cutoff,
                TestCaseHistory.duration_ms > 0,
                TestCaseHistory.status.in_(_DURATION_STATUSES),
            )
        )
    ).first()
    n = int(getattr(row, "n", 0) or 0) if row is not None else 0
    p50 = getattr(row, "p50", None)
    if row is None or n <= 0 or p50 is None:
        return out
    p95 = getattr(row, "p95", None)
    max_ms = getattr(row, "max_ms", None)
    out.update({
        "n": n,
        "p50_ms": round(float(p50), 2),
        "p95_ms": round(float(p95), 2) if p95 is not None else None,
        "max_ms": int(max_ms) if max_ms is not None else None,
        "measured": True,
        "reason": None,
    })
    return out


async def _baseline(
    db: AsyncSession,
    project_id: uuid.UUID,
    fingerprint: str | None,
) -> dict | None:
    """EXEC-03: the test's ``perf_baselines`` row in THIS project, or None."""
    if not fingerprint:
        return None
    from app.services.execution_metrics_service import load_baselines

    return (await load_baselines(db, project_id, [fingerprint])).get(fingerprint)


# Auto-detector flaky ratio band, copied verbatim from
# ``analytics_service.flaky_tests`` (the /failures driver): its HAVING clause is
# ``... ratio BETWEEN 0.05 AND 0.95``. ``is_flaky`` here gates on the same band
# so this panel agrees with /failures for low/high-but-nonzero ratios.
_FLAKY_RATIO_LOW = 0.05
_FLAKY_RATIO_HIGH = 0.95


async def _flakiness_in_window(
    db: AsyncSession,
    project_id: uuid.UUID,
    fingerprint: str | None,
) -> dict:
    """Compute the flakiness value over the 30-day flaky-coach/analytics window.

    Aggregates ALL ``test_case_history`` rows for the fingerprint in the last
    ``_FLAKY_WINDOW_DAYS`` days — no per-fingerprint row cap — exactly like
    ``analytics_service.flaky_tests`` (``WHERE tch.created_at >= :period_start``,
    ``COUNT(*)``, no LIMIT per fingerprint). The 50-row ``_history_timeline`` is a
    separate display surface; the denominator here is the true in-window
    population so ``failure_rate``/``failure_rate_pct``/``total_runs``/``passed``/
    ``failed`` match what /failures and /flaky-coach show for the same test.

    ``failure_rate`` (FAILED+BROKEN over total) and ``failure_rate_pct`` (× 100,
    1 dp) match ``flaky_tests``; classification + ``impact_score`` reuse
    ``test_health_coach_service`` thresholds — no new formula. ``is_flaky`` gates
    on the auto-detector's ``>=3 runs`` and ``0.05–0.95`` ratio band (the
    ``flaky_tests`` HAVING clause), so it agrees with /failures.
    """
    total = 0
    failed = 0
    passed = 0
    if fingerprint:
        cutoff = datetime.now(timezone.utc) - timedelta(days=_FLAKY_WINDOW_DAYS)
        agg = (
            select(
                sa_func.count().label("total"),
                sa_func.count()
                .filter(TestCaseHistory.status.in_(_FAILED_STATUSES))
                .label("failed"),
                sa_func.count()
                .filter(TestCaseHistory.status == "PASSED")
                .label("passed"),
            )
            .join(TestRun, TestRun.id == TestCaseHistory.test_run_id)
            .where(
                TestRun.project_id == project_id,
                TestCaseHistory.test_fingerprint == fingerprint,
                TestCaseHistory.created_at >= cutoff,
            )
        )
        agg_row = (await db.execute(agg)).first()
        if agg_row is not None:
            total = agg_row.total or 0
            failed = agg_row.failed or 0
            passed = agg_row.passed or 0

    failure_rate = (failed / total) if total else 0.0
    impact = _compute_impact_score(failure_rate, total) if total else 0.0
    classification = _compute_quarantine_recommendation(failure_rate)

    # The /failures auto-detector (analytics_service.flaky_tests) fires only on
    # >=3 runs with the failure ratio in [0.05, 0.95]. Gate identically so a
    # 2%/98% test reads non-flaky here too, matching what /failures shows.
    is_flaky = total >= 3 and _FLAKY_RATIO_LOW <= failure_rate <= _FLAKY_RATIO_HIGH

    return {
        "is_flaky": is_flaky,
        "failure_rate": round(failure_rate, 4),
        "failure_rate_pct": round(failure_rate * 100.0, 1),
        "impact_score": impact,
        "classification": classification,
        "window_days": _FLAKY_WINDOW_DAYS,
        "total_runs": total,
        "passed": passed,
        "failed": failed,
    }


async def _metadata(
    db: AsyncSession,
    project_id: uuid.UUID,
    fingerprint: str | None,
    tc: TestCase,
    run: TestRun,
) -> dict:
    """Identity metadata: owner, effective suite, first/last seen, timestamps."""
    # Effective suite (both tc.suite_name and tr.primary_suite_name) via the
    # shared SQL expression so the label matches every other suite surface.
    effective_suite = None
    suite_row = (
        await db.execute(
            text(
                f"SELECT {_effective_suite_sql()} AS suite "
                "FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id "
                "WHERE tc.id = :tc_id"
            ),
            {"tc_id": str(tc.id)},
        )
    ).first()
    if suite_row is not None:
        effective_suite = suite_row.suite

    # Canonical identity row (project-scoped by (project_id, fingerprint)) —
    # first/last seen pointers + catalog timestamps.
    first_seen_run_id = None
    first_seen_label = None
    first_seen_at = None
    last_seen_run_id = None
    last_seen_label = None
    last_seen_at = None
    canonical_created_at = None
    canonical_updated_at = None

    if fingerprint:
        ctc = (
            await db.execute(
                select(CanonicalTestCase).where(
                    CanonicalTestCase.project_id == project_id,
                    CanonicalTestCase.test_fingerprint == fingerprint,
                )
            )
        ).scalar_one_or_none()
        if ctc is not None:
            canonical_created_at = ctc.created_at
            canonical_updated_at = ctc.updated_at
            first_seen_run_id = ctc.first_seen_run_id
            last_seen_run_id = ctc.last_seen_run_id
            label_ids = [r for r in (first_seen_run_id, last_seen_run_id) if r]
            if label_ids:
                run_rows = (
                    await db.execute(
                        select(TestRun.id, TestRun.build_number, TestRun.created_at)
                        .where(TestRun.id.in_(label_ids))
                    )
                ).all()
                by_id = {rr.id: rr for rr in run_rows}
                if first_seen_run_id and first_seen_run_id in by_id:
                    first_seen_label = by_id[first_seen_run_id].build_number
                    first_seen_at = by_id[first_seen_run_id].created_at
                if last_seen_run_id and last_seen_run_id in by_id:
                    last_seen_label = by_id[last_seen_run_id].build_number
                    last_seen_at = by_id[last_seen_run_id].created_at

    return {
        "owner": tc.owner,
        "assigned_to_user_id": str(tc.assigned_to_user_id) if tc.assigned_to_user_id else None,
        "suite": effective_suite,
        "severity": tc.severity,
        "feature": tc.feature,
        "first_seen_run_id": str(first_seen_run_id) if first_seen_run_id else None,
        "first_seen_run_label": first_seen_label,
        "first_seen_at": first_seen_at,
        "last_seen_run_id": str(last_seen_run_id) if last_seen_run_id else None,
        "last_seen_run_label": last_seen_label,
        "last_seen_at": last_seen_at,
        "created_at": canonical_created_at,
        "updated_at": canonical_updated_at,
    }
