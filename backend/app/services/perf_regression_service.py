"""
Performance / duration regression detection — Tier 2 item 10.

Maintains per-test rolling statistics (``PerfBaseline``) via Welford's
online algorithm so a run can answer "is test X's latest duration a
statistical outlier?" in O(1) per test.

Two writers feed the baselines, and each row is fed exactly once:

* **On ingest** (EXEC-05): ``duration_regression_service`` judges the run
  against the baselines first (``assess_run_durations``), then feeds the
  run's rows (``feed_run_observations``) and stamps the run's
  ``duration_findings.baseline_fed_through``.
* **Nightly** (``refresh_perf_baselines`` beat → ``refresh_baselines``): the
  backfill for rows the on-ingest step did not feed (runs ingested while the
  flag was off, or before EXEC-05). It skips every row a run's
  ``baseline_fed_through`` already covers.

Both take the refresh cursor's row lock first, so a sweep and an on-ingest
feed never interleave. The release gate does NOT call this module: it reads
the stored ``test_runs.duration_findings`` through the opt-in
``perf_regression`` policy rule.

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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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


async def _load_refresh_cursor(
    db: AsyncSession, *, lock: bool = False,
) -> Optional[datetime]:
    """The sweep's high-watermark. ``lock=True`` takes the cursor row's lock
    (``SELECT ... FOR UPDATE``) for the rest of the caller's transaction: the
    nightly sweep and the on-ingest feed both do, so one waits for the other
    and a row is never fed by both (EXEC-05 double-feed guard)."""
    stmt = select(AppSetting).where(AppSetting.key == _REFRESH_CURSOR_KEY)
    if lock:
        stmt = stmt.with_for_update()
    row = await db.execute(stmt)
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


async def _feature_enabled(
    db: Optional[AsyncSession] = None,
    *,
    project_id: Optional[uuid.UUID] = None,
    any_scope: bool = False,
) -> bool:
    """``perf_regression_detection`` for the project whose baselines these are.

    Baselines and spikes are per project. Asked without the project, a project
    allow-list turned detection off everywhere. The nightly refresh spans every
    project: it exits early with ``any_scope=True`` and then records only the
    rows whose project has the flag on.
    """
    try:
        from app.services.feature_flags import is_enabled
        return await is_enabled(
            "perf_regression_detection", db=db, project_id=project_id,
            any_scope=any_scope,
        )
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
    """Pure ``(baseline, obs) -> bool`` spike check, used per test by
    ``assess_run_durations`` with no extra round-trip."""
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

    if not await _feature_enabled(any_scope=True):
        perf_baseline_refresh_runs_total.labels(status="skipped").inc()
        return {"observed": 0, "baselines": 0, "skipped": 1}

    observed = 0
    skipped_fed_on_ingest = 0
    baselines_touched: set[tuple[uuid.UUID, str]] = set()

    async with AsyncSessionLocal() as db:
        # Incremental, exactly-once sweep: only rows created after the last
        # watermark, oldest-first so the cursor advances monotonically and a
        # backlog larger than one batch isn't stranded. ASC + ``created_at >
        # cursor`` (rather than DESC top-N) is what makes re-runs idempotent —
        # the previous code re-fed the most-recent rows every night.
        cursor = await _load_refresh_cursor(db, lock=True)
        # ``test_cases`` has no project_id: the project comes from the run.
        # (The sweep used to read ``TestCase.project_id``, which does not
        # exist, so with the flag on it raised on the first real row -- it was
        # only ever exercised against test doubles that carried one.)
        stmt = (
            select(
                TestCase.test_run_id,
                TestCase.test_fingerprint,
                TestCase.test_name,
                TestCase.suite_name,
                TestCase.duration_ms,
                TestCase.created_at,
                TestRun.project_id,
            )
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(
                TestCase.duration_ms.is_not(None),
                TestCase.duration_ms > 0,
                TestCase.status.in_((TestStatus.PASSED.value, TestStatus.FAILED.value)),
            )
        )
        if cursor is not None:
            stmt = stmt.where(TestCase.created_at > cursor)
        stmt = stmt.order_by(TestCase.created_at.asc()).limit(_REFRESH_BATCH_SIZE)

        rows = list((await db.execute(stmt)).all())

        # Rows the on-ingest evaluation already fed (EXEC-05). They are swept
        # past -- the watermark still advances over them -- but never fed a
        # second time: re-feeding a row inflates sample_count and corrupts
        # mean/stddev, and nothing could tell afterwards.
        fed_through = await _runs_fed_through(
            db, {getattr(tc, "test_run_id", None) for tc in rows},
        )

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

        # The flag is per project: a row of a project with the flag off is
        # swept past (the watermark still advances) but never observed.
        project_on: dict[uuid.UUID, bool] = {}
        max_created: Optional[datetime] = None
        for tc in rows:
            # Advance the watermark for every swept row, even ones we skip
            # below (no fingerprint), so they aren't re-examined next run.
            if tc.created_at and (max_created is None or tc.created_at > max_created):
                max_created = tc.created_at
            if not tc.test_fingerprint:
                continue
            run_id = getattr(tc, "test_run_id", None)
            run_fed_through = fed_through.get(run_id) if run_id is not None else None
            if (
                run_fed_through is not None
                and tc.created_at is not None
                and tc.created_at <= run_fed_through
            ):
                skipped_fed_on_ingest += 1
                continue
            if tc.project_id not in project_on:
                project_on[tc.project_id] = await _feature_enabled(
                    db, project_id=tc.project_id,
                )
            if not project_on[tc.project_id]:
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
        skipped_fed_on_ingest=skipped_fed_on_ingest,
    )
    perf_baseline_refresh_runs_total.labels(status="success").inc()
    return {"observed": observed, "baselines": len(baselines_touched), "skipped": 0}


# ── Spike detection (run page, release gate rule, notifications) ──────────

# Fingerprints per ``IN (...)`` lookup: one bind parameter each, far below
# asyncpg's 32767 limit even for the largest run the ingest cap admits.
_IN_CHUNK = 2000


def _chunks(values: list[str], size: int = _IN_CHUNK) -> list[list[str]]:
    return [values[i:i + size] for i in range(0, len(values), size)]


def _sigma(baseline: PerfBaseline, observation_ms: int) -> float:
    return round(
        (observation_ms - float(baseline.mean_ms or 0.0))
        / max(float(baseline.stddev_ms or 1.0), 1.0),
        2,
    )


@dataclass
class RunDurationAssessment:
    """One run's PASSED tests judged against their baselines.

    ``spikes`` is every test whose duration is a spike (most extreme first,
    unbounded: the caller caps what it stores). ``tests_considered`` counts the
    run's PASSED tests that carry a fingerprint and a duration; ``tests_judged``
    those whose baseline has the minimum history; ``max_samples`` is the
    deepest baseline among them -- the "n" of "not enough history (n of 10)".
    """
    spikes: list[dict[str, Any]] = field(default_factory=list)
    tests_considered: int = 0
    tests_judged: int = 0
    max_samples: int = 0
    min_samples: int = _MIN_SAMPLES_FOR_SPIKE


async def assess_run_durations(
    db: AsyncSession,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    sigma: float = _SPIKE_SIGMA,
) -> RunDurationAssessment:
    """Judge a run's PASSED test durations against the project's baselines.

    Read-only, no flag check (the caller decides whether the project measures
    spikes). Call it BEFORE ``feed_run_observations``: a baseline that already
    holds this run's observation is diluted by the very outlier it is asked
    about (one 400 ms run in a 100 +/- 10 ms series of ten moves the mean to
    127 ms and the stddev to 91 ms, and the run reads 3.0 sigma instead of 30).

    Each spike also says whether the test was ALREADY slow on its previous
    observation (``baseline.last_observed_ms``), so the caller can notify on
    the first slow run of a streak and not on every one.
    """
    assessment = RunDurationAssessment()
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
    tests: dict[str, dict[str, Any]] = {}
    for row in tc_result.all():
        if not row.test_fingerprint or row.test_fingerprint in tests:
            continue  # duplicates inside one run (retries): the first wins
        tests[row.test_fingerprint] = {
            "test_name": row.test_name,
            "suite_name": row.suite_name,
            "duration_ms": int(row.duration_ms),
        }
    assessment.tests_considered = len(tests)
    if not tests:
        return assessment

    baselines: dict[str, PerfBaseline] = {}
    for chunk in _chunks(list(tests)):
        bs_result = await db.execute(
            select(PerfBaseline).where(
                PerfBaseline.project_id == project_id,
                PerfBaseline.test_fingerprint.in_(chunk),
            )
        )
        baselines.update({b.test_fingerprint: b for b in bs_result.scalars().all()})

    for fingerprint, test in tests.items():
        baseline = baselines.get(fingerprint)
        if baseline is None:
            continue
        samples = int(baseline.sample_count or 0)
        assessment.max_samples = max(assessment.max_samples, samples)
        if samples < _MIN_SAMPLES_FOR_SPIKE:
            continue
        assessment.tests_judged += 1
        observed = test["duration_ms"]
        if not is_spike(baseline, observed, sigma=sigma):
            continue
        mean = float(baseline.mean_ms or 0.0)
        previous = baseline.last_observed_ms
        assessment.spikes.append({
            "test_fingerprint": fingerprint,
            "test_name": test["test_name"],
            "suite_name": test["suite_name"],
            "observed_ms": observed,
            "baseline_mean_ms": round(mean, 1),
            "baseline_stddev_ms": round(float(baseline.stddev_ms or 0.0), 1),
            "baseline_p95_ms": round(float(baseline.p95_ms or 0.0), 1),
            "sample_count": samples,
            "sigma": _sigma(baseline, observed),
            "delta_ms": int(round(observed - mean)),
            "ratio": round(observed / mean, 2) if mean > 0 else None,
            "previous_ms": int(previous) if previous else None,
            "was_slow_before": bool(
                previous and is_spike(baseline, int(previous), sigma=sigma)
            ),
        })

    # Most extreme first.
    assessment.spikes.sort(key=lambda spike: -spike["sigma"])
    return assessment


async def _runs_fed_through(
    db: AsyncSession, run_ids: set[Any],
) -> dict[uuid.UUID, datetime]:
    """``run_id -> baseline_fed_through`` for the runs whose rows the on-ingest
    evaluation already fed into the baselines (EXEC-05)."""
    ids = [run_id for run_id in run_ids if run_id is not None]
    if not ids:
        return {}
    marker = TestRun.duration_findings["baseline_fed_through"].astext
    result = await db.execute(
        select(TestRun.id, marker.label("fed_through")).where(
            TestRun.id.in_(ids), marker.is_not(None),
        )
    )
    fed: dict[uuid.UUID, datetime] = {}
    for row in result.all():
        try:
            fed[row.id] = datetime.fromisoformat(row.fed_through)
        except (TypeError, ValueError):
            logger.warning(
                "perf_baseline fed-through marker unparseable",
                run_id=str(row.id), raw=row.fed_through,
            )
    return fed


@dataclass
class FeedResult:
    """What ``feed_run_observations`` fed: ``fed_through`` is the newest
    ``created_at`` it fed (the run's marker), ``None`` when it fed nothing."""
    observed: int = 0
    fed_through: Optional[datetime] = None


async def feed_run_observations(
    db: AsyncSession, project_id: uuid.UUID, run_id: uuid.UUID,
) -> FeedResult:
    """Feed one run's rows into its project's baselines, on ingest.

    The same rows the nightly sweep would feed (PASSED or FAILED, a duration,
    a fingerprint), minus the ones the sweep already did: a row at or below
    the sweep's cursor was swept. The cursor row is locked first, as the sweep
    locks it, so the two never interleave. The caller writes the returned
    ``fed_through`` to ``test_runs.duration_findings.baseline_fed_through`` in
    the SAME transaction, and the sweep then skips every row it covers.

    Stage-only: it never commits; the caller does.
    """
    # The lock needs a row. Before the first nightly sweep there is none, and
    # a sweep running then would not wait. An empty cursor ({} = "sweep from
    # the start") makes one; a sweep that raced it conflicts on the key when
    # it saves its own cursor and rolls back, rather than feeding twice.
    await db.execute(
        pg_insert(AppSetting)
        .values(key=_REFRESH_CURSOR_KEY, value={})
        .on_conflict_do_nothing(index_elements=[AppSetting.key])
    )
    cursor = await _load_refresh_cursor(db, lock=True)
    stmt = select(
        TestCase.test_fingerprint,
        TestCase.test_name,
        TestCase.suite_name,
        TestCase.duration_ms,
        TestCase.created_at,
    ).where(
        TestCase.test_run_id == run_id,
        TestCase.duration_ms.is_not(None),
        TestCase.duration_ms > 0,
        TestCase.status.in_((TestStatus.PASSED.value, TestStatus.FAILED.value)),
        TestCase.test_fingerprint.is_not(None),
    )
    if cursor is not None:
        stmt = stmt.where(TestCase.created_at > cursor)
    rows = list((await db.execute(stmt.order_by(TestCase.created_at.asc()))).all())
    result = FeedResult()
    if not rows:
        return result

    by_fp: dict[str, PerfBaseline] = {}
    for chunk in _chunks(sorted({row.test_fingerprint for row in rows})):
        existing = await db.execute(
            select(PerfBaseline).where(
                PerfBaseline.project_id == project_id,
                PerfBaseline.test_fingerprint.in_(chunk),
            )
        )
        by_fp.update({b.test_fingerprint: b for b in existing.scalars().all()})

    for row in rows:
        baseline = await record_observation(
            db,
            project_id,
            row.test_fingerprint,
            duration_ms=int(row.duration_ms),
            test_name=row.test_name,
            suite_name=row.suite_name,
            existing=by_fp.get(row.test_fingerprint),
        )
        if baseline is not None:
            # A second row of the same test in this run accumulates into the
            # same (possibly just created) baseline, as in the sweep.
            by_fp[row.test_fingerprint] = baseline
            result.observed += 1
        if row.created_at is not None and (
            result.fed_through is None or row.created_at > result.fed_through
        ):
            result.fed_through = row.created_at
    return result


async def detect_spikes_for_run(
    db: AsyncSession, run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Return the list of duration spikes for a single run, ``[]`` when the
    project does not measure them (``perf_regression_detection`` off).

    A convenience over ``assess_run_durations`` for callers that only want the
    spikes; ``[]`` here cannot tell "no spike" from "not measured", so the run
    page and the release gate read ``duration_regression_service`` instead.
    """
    if not await _feature_enabled(db, any_scope=True):
        return []

    # Find the run's project so we can limit the baseline lookup.
    run_row = await db.execute(
        select(TestRun.project_id).where(TestRun.id == run_id)
    )
    project_id = run_row.scalar_one_or_none()
    if project_id is None:
        return []
    if not await _feature_enabled(db, project_id=project_id):
        return []

    assessment = await assess_run_durations(db, project_id, run_id)
    return assessment.spikes


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
