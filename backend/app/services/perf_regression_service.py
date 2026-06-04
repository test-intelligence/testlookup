"""
Performance / duration regression detection — Tier 2 item 10.

Maintains per-test rolling statistics (``PerfBaseline``) via Welford's
online algorithm so release-gate scoring can answer "is test X's
latest duration a statistical outlier?" in O(1) per test. A nightly
Celery beat task (``refresh_perf_baselines``) sweeps newly-ingested
``TestCase`` rows into their baselines; the release risk agent reads
``detect_spikes_for_run`` at scoring time.

Welford's algorithm keeps three values per series:

* ``sample_count`` — n
* ``mean_ms``      — running mean
* ``m2``           — sum of squared deviations from the mean

From these, ``stddev_ms = sqrt(m2 / (n - 1))``. Each new observation
updates all three in O(1):

    delta = x - mean
    mean += delta / n
    delta2 = x - mean
    m2 += delta * delta2

Spikes are detected via a simple 3σ rule: a test's newest duration
counts as a spike when ``(observed - mean) >= 3 * stddev`` **and** the
baseline has at least 10 samples (so a new test with low variance
doesn't produce false positives).

Gated behind the ``perf_regression_detection`` feature flag so
existing deployments see zero behaviour change until enabled.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import AppSetting, PerfBaseline, TestCase, TestRun, TestStatus

logger = structlog.get_logger("services.perf_regression")


# Minimum sample count before we'll emit a spike — below this the
# baseline is too fragile to trust as a threshold.
_MIN_SAMPLES_FOR_SPIKE = 10

# Number of standard deviations above the mean that counts as a spike.
_SPIKE_SIGMA = 3.0

# Cap on how many historic TestCase rows to sweep into a baseline per
# refresh pass — keeps the nightly task bounded on huge tables.
_REFRESH_BATCH_SIZE = 5000

# app_settings key holding the high-watermark (max TestCase.created_at
# already swept). Welford is additive — re-feeding a row inflates
# sample_count and corrupts mean/stddev — so the sweep MUST process each
# row exactly once. The cursor makes the nightly pass incremental.
_REFRESH_CURSOR_KEY = "perf_baseline.refresh_cursor"


async def _load_refresh_cursor(db: AsyncSession) -> Optional[datetime]:
    row = await db.execute(
        select(AppSetting).where(AppSetting.key == _REFRESH_CURSOR_KEY)
    )
    setting = row.scalar_one_or_none()
    if setting and setting.value:
        raw = setting.value.get("last_created_at")
        if raw:
            try:
                return datetime.fromisoformat(raw)
            except (ValueError, TypeError):
                logger.warning("perf_baseline cursor unparseable", raw=raw)
    return None


async def _save_refresh_cursor(db: AsyncSession, value: datetime) -> None:
    row = await db.execute(
        select(AppSetting).where(AppSetting.key == _REFRESH_CURSOR_KEY)
    )
    setting = row.scalar_one_or_none()
    if setting is None:
        setting = AppSetting(key=_REFRESH_CURSOR_KEY)
        db.add(setting)
    setting.value = {"last_created_at": value.isoformat()}


# ── Feature flag gate ─────────────────────────────────────────────────────


async def _feature_enabled(db: Optional[AsyncSession] = None) -> bool:
    try:
        from app.services.feature_flags import is_enabled
        return await is_enabled("perf_regression_detection", db=db)
    except Exception as exc:
        logger.debug("perf_regression flag check failed", error=str(exc))
        return False


# ── Welford accumulator ───────────────────────────────────────────────────


def _welford_update(
    current: PerfBaseline, observation_ms: int,
) -> None:
    """In-place Welford update. Pure function — no DB I/O."""
    if observation_ms is None or observation_ms <= 0:
        return
    current.sample_count = int(current.sample_count or 0) + 1
    n = current.sample_count
    prev_mean = float(current.mean_ms or 0.0)
    delta = observation_ms - prev_mean
    current.mean_ms = prev_mean + (delta / n)
    delta2 = observation_ms - current.mean_ms
    current.m2 = float(current.m2 or 0.0) + (delta * delta2)
    if n >= 2:
        current.stddev_ms = math.sqrt(current.m2 / (n - 1))
    else:
        # Variance is undefined for a single sample. Set 0.0 explicitly:
        # a freshly-constructed PerfBaseline has stddev_ms=None until its
        # column default is applied at INSERT, and this runs before any
        # flush — so without this the p95 line below does float(None).
        current.stddev_ms = 0.0
    # p95 is approximated as mean + 1.645*stddev (one-tailed 95%
    # confidence under the normal distribution). Good enough for the
    # release-gate UI; callers wanting the true empirical p95 can query
    # TestCase directly.
    current.p95_ms = float(current.mean_ms or 0.0) + 1.645 * float(current.stddev_ms or 0.0)
    current.last_observed_ms = int(observation_ms)
    current.last_observed_at = datetime.now(timezone.utc)


def is_spike(
    baseline: PerfBaseline, observation_ms: int,
    *,
    sigma: float = _SPIKE_SIGMA,
) -> bool:
    """Pure ``(baseline, obs) -> bool`` spike check. Used directly by
    the release risk agent at scoring time so no new round-trip per
    test is needed."""
    if baseline is None or observation_ms is None or observation_ms <= 0:
        return False
    if int(baseline.sample_count or 0) < _MIN_SAMPLES_FOR_SPIKE:
        return False
    stddev = float(baseline.stddev_ms or 0.0)
    if stddev <= 0:
        return False
    mean = float(baseline.mean_ms or 0.0)
    return (observation_ms - mean) >= sigma * stddev


# ── Refresh / upsert entry points ─────────────────────────────────────────


# Sentinel for record_observation's ``existing`` param: distinguishes "caller
# prefetched the baseline (possibly None → create)" from "not provided → query".
_UNSET = object()


async def record_observation(
    db: AsyncSession,
    project_id: uuid.UUID,
    test_fingerprint: str,
    *,
    duration_ms: int,
    test_name: Optional[str] = None,
    suite_name: Optional[str] = None,
    existing=_UNSET,
) -> Optional[PerfBaseline]:
    """Upsert a baseline row with a single observation.

    Used by ``refresh_perf_baselines`` when sweeping new TestCase rows.

    ``existing`` lets a batched caller pass the already-prefetched
    (project_id, test_fingerprint) baseline — or ``None`` when none exists —
    to skip the per-row SELECT (``refresh_baselines`` prefetches the whole
    batch in one query). When omitted (``_UNSET``) the baseline is queried
    inline, as before. Thread-safe via the (project_id, test_fingerprint)
    unique constraint.
    """
    if duration_ms is None or duration_ms <= 0:
        return None

    if existing is _UNSET:
        result = await db.execute(
            select(PerfBaseline).where(
                PerfBaseline.project_id == project_id,
                PerfBaseline.test_fingerprint == test_fingerprint,
            )
        )
        baseline = result.scalar_one_or_none()
    else:
        baseline = existing
    if baseline is None:
        baseline = PerfBaseline(
            project_id=project_id,
            test_fingerprint=test_fingerprint,
            test_name=test_name,
            suite_name=suite_name,
        )
        db.add(baseline)

    _welford_update(baseline, int(duration_ms))
    if test_name and not baseline.test_name:
        baseline.test_name = test_name
    if suite_name and not baseline.suite_name:
        baseline.suite_name = suite_name
    return baseline


async def refresh_baselines() -> dict[str, int]:
    """Nightly maintenance task.

    Sweeps ``TestCase`` rows that (a) have a non-zero duration, (b)
    came from a PASSED or FAILED status (SKIPPED/BROKEN are excluded so
    infra failures don't pollute the latency signal), and (c) were
    created since the most recent baseline's ``last_observed_at``.

    Feature-flag gated. Returns telemetry counts for the celery task.
    """
    from app.core.metrics import perf_baseline_refresh_runs_total

    if not await _feature_enabled():
        perf_baseline_refresh_runs_total.labels(status="skipped").inc()
        return {"observed": 0, "baselines": 0, "skipped": 1}

    observed = 0
    baselines_touched: set[tuple[uuid.UUID, str]] = set()

    async with AsyncSessionLocal() as db:
        # Incremental, exactly-once sweep: only rows created after the last
        # watermark, oldest-first so the cursor advances monotonically and a
        # backlog larger than one batch isn't stranded. ASC + ``created_at >
        # cursor`` (rather than DESC top-N) is what makes re-runs idempotent —
        # the previous code re-fed the most-recent rows every night.
        cursor = await _load_refresh_cursor(db)
        stmt = select(TestCase).where(
            TestCase.duration_ms.is_not(None),
            TestCase.duration_ms > 0,
            TestCase.status.in_((TestStatus.PASSED.value, TestStatus.FAILED.value)),
        )
        if cursor is not None:
            stmt = stmt.where(TestCase.created_at > cursor)
        stmt = stmt.order_by(TestCase.created_at.asc()).limit(_REFRESH_BATCH_SIZE)

        rows = list((await db.execute(stmt)).scalars().all())

        # Prefetch the existing baselines for this batch in ONE query instead
        # of a per-row SELECT inside record_observation (was a 1+N N+1 over the
        # nightly sweep of up to _REFRESH_BATCH_SIZE rows). The IN x IN can
        # over-fetch unrelated (project, fingerprint) combos, but we only ever
        # look up the real batch pairs, so spurious dict entries are never read.
        fingerprints = {tc.test_fingerprint for tc in rows if tc.test_fingerprint}
        by_pair: dict[tuple[uuid.UUID, str], PerfBaseline] = {}
        if fingerprints:
            project_ids = {tc.project_id for tc in rows if tc.test_fingerprint}
            existing_rows = (
                await db.execute(
                    select(PerfBaseline).where(
                        PerfBaseline.project_id.in_(project_ids),
                        PerfBaseline.test_fingerprint.in_(fingerprints),
                    )
                )
            ).scalars().all()
            by_pair = {(b.project_id, b.test_fingerprint): b for b in existing_rows}

        max_created: Optional[datetime] = None
        for tc in rows:
            # Advance the watermark for every swept row, even ones we skip
            # below (no fingerprint), so they aren't re-examined next run.
            if tc.created_at and (max_created is None or tc.created_at > max_created):
                max_created = tc.created_at
            if not tc.test_fingerprint:
                continue
            pair = (tc.project_id, tc.test_fingerprint)
            baseline = await record_observation(
                db,
                tc.project_id,
                tc.test_fingerprint,
                duration_ms=int(tc.duration_ms),
                test_name=tc.test_name,
                suite_name=tc.suite_name,
                existing=by_pair.get(pair),
            )
            # Cache the (new or existing) baseline so a later row with the SAME
            # (project, fingerprint) accumulates into it via Welford rather than
            # creating a duplicate — the previous per-row SELECT relied on
            # autoflush to see the just-created row; the unique constraint
            # uq_perf_baseline_fingerprint would otherwise raise on commit.
            if baseline is not None:
                by_pair[pair] = baseline
            observed += 1
            baselines_touched.add(pair)
        if max_created is not None:
            await _save_refresh_cursor(db, max_created)
            await db.commit()

    logger.info(
        "perf_baselines_refresh",
        observed=observed,
        distinct_baselines=len(baselines_touched),
    )
    perf_baseline_refresh_runs_total.labels(status="success").inc()
    return {"observed": observed, "baselines": len(baselines_touched), "skipped": 0}


# ── Spike detection (release gate integration) ───────────────────────────


async def detect_spikes_for_run(
    db: AsyncSession, run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Return the list of duration spikes for a single run.

    Called from the release risk agent. Looks up the run's project,
    joins to every TestCase in the run, fetches the matching
    ``PerfBaseline`` rows, and returns one dict per spike with
    enough metadata for the release gate UI to render a "top perf
    regressions" section.
    """
    if not await _feature_enabled(db):
        return []

    # Find the run's project so we can limit the baseline lookup.
    run_row = await db.execute(
        select(TestRun.project_id).where(TestRun.id == run_id)
    )
    project_id = run_row.scalar_one_or_none()
    if project_id is None:
        return []

    # Pull all durations for this run in one shot.
    tc_result = await db.execute(
        select(
            TestCase.test_fingerprint,
            TestCase.test_name,
            TestCase.suite_name,
            TestCase.duration_ms,
        ).where(
            TestCase.test_run_id == run_id,
            TestCase.duration_ms.is_not(None),
            TestCase.duration_ms > 0,
            TestCase.status == TestStatus.PASSED.value,
        )
    )
    tests = [
        {
            "test_fingerprint": row.test_fingerprint,
            "test_name": row.test_name,
            "suite_name": row.suite_name,
            "duration_ms": int(row.duration_ms),
        }
        for row in tc_result.all()
        if row.test_fingerprint
    ]
    if not tests:
        return []

    # Batch-load baselines for every fingerprint in the run.
    fingerprints = [t["test_fingerprint"] for t in tests]
    bs_result = await db.execute(
        select(PerfBaseline).where(
            PerfBaseline.project_id == project_id,
            PerfBaseline.test_fingerprint.in_(fingerprints),
        )
    )
    baselines = {b.test_fingerprint: b for b in bs_result.scalars().all()}

    spikes: list[dict[str, Any]] = []
    for t in tests:
        baseline = baselines.get(t["test_fingerprint"])
        if baseline is None:
            continue
        if not is_spike(baseline, t["duration_ms"]):
            continue
        spikes.append({
            "test_fingerprint": t["test_fingerprint"],
            "test_name": t["test_name"],
            "suite_name": t["suite_name"],
            "observed_ms": t["duration_ms"],
            "baseline_mean_ms": round(float(baseline.mean_ms or 0.0), 1),
            "baseline_stddev_ms": round(float(baseline.stddev_ms or 0.0), 1),
            "baseline_p95_ms": round(float(baseline.p95_ms or 0.0), 1),
            "sample_count": int(baseline.sample_count or 0),
            "sigma": (
                round(
                    (t["duration_ms"] - float(baseline.mean_ms or 0.0))
                    / max(float(baseline.stddev_ms or 1.0), 1.0),
                    2,
                )
            ),
        })

    # Sort most extreme first.
    spikes.sort(key=lambda s: -s["sigma"])
    return spikes


async def list_top_baselines(
    db: AsyncSession, project_id: uuid.UUID, *, limit: int = 50,
) -> list[PerfBaseline]:
    """Return the top-N slowest tests (by p95) for the dashboard."""
    result = await db.execute(
        select(PerfBaseline)
        .where(PerfBaseline.project_id == project_id)
        .order_by(PerfBaseline.p95_ms.desc().nulls_last())
        .limit(min(max(1, limit), 200))
    )
    return list(result.scalars().all())
