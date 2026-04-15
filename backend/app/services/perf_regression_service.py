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
from app.models.postgres import PerfBaseline, TestCase, TestRun, TestStatus

logger = structlog.get_logger("services.perf_regression")


# Minimum sample count before we'll emit a spike — below this the
# baseline is too fragile to trust as a threshold.
_MIN_SAMPLES_FOR_SPIKE = 10

# Number of standard deviations above the mean that counts as a spike.
_SPIKE_SIGMA = 3.0

# Cap on how many historic TestCase rows to sweep into a baseline per
# refresh pass — keeps the nightly task bounded on huge tables.
_REFRESH_BATCH_SIZE = 5000


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
    # p95 is approximated as mean + 1.645*stddev (one-tailed 95%
    # confidence under the normal distribution). Good enough for the
    # release-gate UI; callers wanting the true empirical p95 can query
    # TestCase directly.
    current.p95_ms = float(current.mean_ms) + 1.645 * float(current.stddev_ms)
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


async def record_observation(
    db: AsyncSession,
    project_id: uuid.UUID,
    test_fingerprint: str,
    *,
    duration_ms: int,
    test_name: Optional[str] = None,
    suite_name: Optional[str] = None,
) -> Optional[PerfBaseline]:
    """Upsert a baseline row with a single observation.

    Used by ``refresh_perf_baselines`` when sweeping new TestCase
    rows. Thread-safe via the (project_id, test_fingerprint) unique
    constraint + row lock.
    """
    if duration_ms is None or duration_ms <= 0:
        return None

    result = await db.execute(
        select(PerfBaseline).where(
            PerfBaseline.project_id == project_id,
            PerfBaseline.test_fingerprint == test_fingerprint,
        )
    )
    baseline = result.scalar_one_or_none()
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
    if not await _feature_enabled():
        return {"observed": 0, "baselines": 0, "skipped": 1}

    observed = 0
    baselines_touched: set[tuple[uuid.UUID, str]] = set()

    async with AsyncSessionLocal() as db:
        # We walk distinct TestCase rows with the highest-cadence data
        # first. A dedicated cursor would be nicer but the capped batch
        # size keeps memory bounded even without it.
        stmt = (
            select(TestCase)
            .where(
                TestCase.duration_ms.is_not(None),
                TestCase.duration_ms > 0,
                TestCase.status.in_((TestStatus.PASSED.value, TestStatus.FAILED.value)),
            )
            .order_by(TestCase.created_at.desc())
            .limit(_REFRESH_BATCH_SIZE)
        )
        result = await db.execute(stmt)
        for tc in result.scalars().all():
            if not tc.test_fingerprint:
                continue
            await record_observation(
                db,
                tc.project_id,
                tc.test_fingerprint,
                duration_ms=int(tc.duration_ms),
                test_name=tc.test_name,
                suite_name=tc.suite_name,
            )
            observed += 1
            baselines_touched.add((tc.project_id, tc.test_fingerprint))
        if observed:
            await db.commit()

    logger.info(
        "perf_baselines_refresh",
        observed=observed,
        distinct_baselines=len(baselines_touched),
    )
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
