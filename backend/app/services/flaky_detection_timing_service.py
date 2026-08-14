"""Tier 2, and the measurement that keeps both tiers honest.

Phase 6 of ``architecture/TEST_INTELLIGENCE_PLAN.md`` — the last phase.

Tier 1 (``flaky_screening_service``) looks at the new and the directly-modified
for fast feedback. Tier 2 is this: a continuous background pass over the whole
corpus, which is where environment- and dependency-induced flakiness lands —
the tail that screening a diff cannot reach by construction.

## Why the cadence is time-based, not commit-based

The source finding for tier 1 is usually paired with a ~150-commit re-run
budget. That figure is unusable here, and not because it is wrong: the Phase 0
census measured **zero of four genuine projects on the reference deployment**
clearing the thresholds a commit-count cadence assumes. Those projects carry
12–15 test fingerprints with a median of 5–12 runs each, and most runs arrive
with no commit range attached at all. A cadence expressed in commits would, on
this corpus, be a cadence that never fires.

So cadence is expressed in time and derived from each project's own measured
arrival rate — screening tracks how fast runs arrive, while the whole-corpus
sweep tracks the score's own 30-day window, which does not move meaningfully
inside a day.

## The finding this module exists to publish

Detection latency on a thin corpus is dominated by **run frequency, not sweep
frequency**. A new test cannot be scored until it clears the evidence floor
(``MIN_OBSERVATIONS``), and on a project producing a run a day that is days
away no matter how often the beat runs. Cranking the cadence would look like
progress and deliver none, so this service computes both terms and names which
one dominates, per project, from measured numbers.

## Honesty contract

Latency is only reported over fingerprints whose first appearance was actually
observed (``first_seen_is_exact``). Everything that predates screening is
counted and excluded, with the exclusion reported — a backfilled first-seen
would manufacture a flattering zero. Retention purges can delete the older runs
that would have marked a fingerprint as pre-existing; that limitation is stated
in the payload rather than hidden.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional, Sequence

import structlog

from app.services.flaky_score_service import MIN_OBSERVATIONS
from app.services.flaky_screening_service import (
    SCREEN_CORPUS_SWEEP,
    ScreeningCandidate,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger("services.flaky_detection_timing")

MINUTES_PER_DAY = 1440

# Screening bounds. The floor stops a busy project scheduling a beat faster
# than it can finish; the ceiling stops a quiet project's screening degrading
# into "the nightly sweep, again".
MIN_SCREEN_INTERVAL_MINUTES = 30
MAX_SCREEN_INTERVAL_MINUTES = MINUTES_PER_DAY

# The whole-corpus pass. Fixed at nightly because it is measured against the
# score's own 30-day window: a corpus-wide statistic does not move enough
# inside a day to justify re-reading hundreds of thousands of rows.
SWEEP_INTERVAL_MINUTES = MINUTES_PER_DAY

# A row not swept within this multiple of the sweep interval is stale. Two
# intervals, so a single skipped beat is not reported as rot.
STALE_SWEEP_MULTIPLIER = 2

# The measurement endpoint sits on a request path, so its read is bounded. A
# capped sample that says it was capped beats a slow endpoint — and beats a
# silent truncation that would read as full coverage.
MAX_TIMING_ROWS = 20_000

DOMINATED_BY_RUN_FREQUENCY = "run_frequency"
DOMINATED_BY_SWEEP_CADENCE = "sweep_cadence"
DOMINANT_TERMS: tuple[str, ...] = (
    DOMINATED_BY_RUN_FREQUENCY,
    DOMINATED_BY_SWEEP_CADENCE,
)


# ── Pure computations ────────────────────────────────────────────────────────


@dataclass
class Cadence:
    screen_interval_minutes: int
    sweep_interval_minutes: int
    basis: str
    runs_per_day: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "screen_interval_minutes": self.screen_interval_minutes,
            "sweep_interval_minutes": self.sweep_interval_minutes,
            "basis": self.basis,
            "runs_per_day": round(self.runs_per_day, 3),
            "why_not_commit_based": (
                "A commit-count cadence assumes a commit range on most runs and "
                "enough of them to count; the Phase 0 census measured zero of four "
                "genuine projects clearing that. Cadence is therefore expressed in "
                "time and derived from this project's own measured arrival rate."
            ),
        }


def recommend_cadence(runs_per_day: float) -> Cadence:
    """Screening cadence from a project's own measured arrival rate.

    Screening more often than runs arrive re-reads the same window for no new
    information; screening much less often than they arrive is just the nightly
    sweep with extra steps. Half the inter-run interval, clamped, sits between
    those and needs no constant borrowed from someone else's corpus.
    """
    try:
        rate = float(runs_per_day)
    except (TypeError, ValueError):
        rate = 0.0
    if rate <= 0:
        # Nothing is arriving. Screening on a fast beat would burn cycles to
        # re-read an unchanged corpus, so fall back to the sweep's cadence and
        # say why rather than reporting a confident-looking interval.
        return Cadence(
            screen_interval_minutes=MAX_SCREEN_INTERVAL_MINUTES,
            sweep_interval_minutes=SWEEP_INTERVAL_MINUTES,
            basis="no runs observed in the measured window",
            runs_per_day=0.0,
        )
    inter_run_minutes = MINUTES_PER_DAY / rate
    interval = int(round(inter_run_minutes / 2))
    interval = max(MIN_SCREEN_INTERVAL_MINUTES, min(MAX_SCREEN_INTERVAL_MINUTES, interval))
    return Cadence(
        screen_interval_minutes=interval,
        sweep_interval_minutes=SWEEP_INTERVAL_MINUTES,
        basis=(
            f"half the measured inter-run interval "
            f"({inter_run_minutes / 60:.1f}h), clamped to "
            f"[{MIN_SCREEN_INTERVAL_MINUTES}, {MAX_SCREEN_INTERVAL_MINUTES}] minutes"
        ),
        runs_per_day=rate,
    )


def expected_minutes_to_floor(
    observation_count: int, runs_per_day: float
) -> Optional[float]:
    """How long until this fingerprint could be scored at all.

    Approximates one observation per run, which holds for a test that runs
    every time and overstates the wait for one that does not. ``None`` when the
    arrival rate is unknown — an unanswerable question gets no number.
    """
    try:
        needed = MIN_OBSERVATIONS - int(observation_count)
        rate = float(runs_per_day)
    except (TypeError, ValueError):
        return None
    if needed <= 0:
        return 0.0
    if rate <= 0:
        return None
    return needed * (MINUTES_PER_DAY / rate)


def dominant_term(
    screen_interval_minutes: float, expected_wait_minutes: Optional[float]
) -> Optional[str]:
    """Which term actually decides how fast a new flaky test is detected.

    Returns ``None`` when the wait is unknown, rather than defaulting to the
    answer that flatters the cadence.
    """
    if expected_wait_minutes is None:
        return None
    if expected_wait_minutes > screen_interval_minutes:
        return DOMINATED_BY_RUN_FREQUENCY
    return DOMINATED_BY_SWEEP_CADENCE


def percentiles(samples: Sequence[float]) -> dict[str, Optional[float]]:
    """Nearest-rank p50/p90 over whatever survived the honesty filter.

    Empty input yields ``None``, not ``0`` — "nothing measurable yet" and
    "measured, instant" are different claims.
    """
    values = sorted(float(v) for v in samples if v is not None)
    if not values:
        return {"p50": None, "p90": None, "max": None}

    def _at(fraction: float) -> float:
        rank = max(1, min(len(values), int(-(-len(values) * fraction // 1))))
        return values[rank - 1]

    return {"p50": _at(0.5), "p90": _at(0.9), "max": values[-1]}


# ── Persistence (services stage; the caller owns the transaction) ────────────


async def record_screening(
    db: "AsyncSession",
    project_id: Any,
    candidates: Sequence[ScreeningCandidate],
) -> int:
    """Write tier-1 observations, creating each fingerprint's state row once.

    ``first_seen_at`` and ``first_seen_is_exact`` are written on creation and
    never updated. Re-deriving them later would make the record track the
    reader's window instead of the test's history, which is the whole thing
    this table exists to avoid.

    Flushes; does not commit.
    """
    from sqlalchemy import select

    from app.models.postgres import FlakyDetectionState

    if not candidates:
        return 0

    # Two beat processes firing at once can both miss an existing row and both
    # insert. The unique index is what actually protects the data; the caller's
    # per-project try/except turns the resulting violation into one skipped
    # project instead of a dead sweep. Read-then-write is fine BECAUSE of that,
    # not in spite of it.
    fingerprints = [c.test_fingerprint for c in candidates]
    existing = {
        row.test_fingerprint: row
        for row in (
            await db.execute(
                select(FlakyDetectionState).where(
                    FlakyDetectionState.project_id == project_id,
                    FlakyDetectionState.test_fingerprint.in_(fingerprints),
                )
            )
        ).scalars().all()
    }

    now = datetime.now(timezone.utc)
    written = 0
    for candidate in candidates:
        row = existing.get(candidate.test_fingerprint)
        if row is None:
            db.add(
                FlakyDetectionState(
                    project_id=project_id,
                    test_fingerprint=candidate.test_fingerprint,
                    test_name=candidate.test_name,
                    first_seen_at=candidate.first_seen_at,
                    first_seen_is_exact=candidate.first_seen_is_exact,
                    screen_reason=candidate.reason,
                    first_screened_at=now,
                    observation_count=candidate.observation_count,
                    updated_at=now,
                )
            )
            written += 1
            continue
        # Established rows only ever gain observations. In particular the
        # reason is not rewritten: a test that was new when we met it does not
        # stop having been new because someone later edited it.
        row.observation_count = max(
            int(row.observation_count or 0), int(candidate.observation_count)
        )
        if row.first_screened_at is None:
            row.first_screened_at = now
        row.updated_at = now
        written += 1

    await db.flush()
    return written


async def sweep_project(db: "AsyncSession", project_id: Any) -> dict[str, int]:
    """Tier 2: the continuous pass over the whole corpus.

    Two jobs, deliberately done as two independent passes rather than by
    loading every state row into memory:

    1. **Close latency clocks.** Walk the project's scores and, for each one
       that has cleared the evidence floor, record the moment the system first
       had a defensible thing to say. Fingerprints met here for the first time
       — everything that predates screening, and everything tier 1 never had a
       reason to look at — are adopted with ``first_seen_is_exact=False``, so
       they are counted but kept out of the latency statistics.
    2. **Stamp coverage.** One bulk UPDATE over the project's rows, so
       ``last_swept_at`` reflects a pass that actually happened and coverage
       cannot be reported from the beat merely having run. Doing this as a
       statement rather than a row loop is what keeps a large corpus from
       having to fit in a worker's memory.

    Flushes; does not commit.
    """
    from sqlalchemy import select, update

    from app.models.postgres import FlakyDetectionState, FlakyScore

    now = datetime.now(timezone.utc)
    # Bounded by construction: the scorer writes at most one row per
    # fingerprint that cleared the floor, and skips the rest entirely.
    scores = list(
        (
            await db.execute(
                select(FlakyScore).where(FlakyScore.project_id == project_id)
            )
        ).scalars().all()
    )

    adopted = closed = 0
    if scores:
        fingerprints = [s.test_fingerprint for s in scores]
        states = {
            row.test_fingerprint: row
            for row in (
                await db.execute(
                    select(FlakyDetectionState).where(
                        FlakyDetectionState.project_id == project_id,
                        FlakyDetectionState.test_fingerprint.in_(fingerprints),
                    )
                )
            ).scalars().all()
        }
        for score in scores:
            row = states.get(score.test_fingerprint)
            if row is None:
                # Met via the sweep, so its first appearance was not observed.
                row = FlakyDetectionState(
                    project_id=project_id,
                    test_fingerprint=score.test_fingerprint,
                    test_name=getattr(score, "test_name", None),
                    first_seen_at=getattr(score, "computed_at", None) or now,
                    first_seen_is_exact=False,
                    screen_reason=SCREEN_CORPUS_SWEEP,
                    observation_count=int(getattr(score, "observation_count", 0) or 0),
                    updated_at=now,
                )
                db.add(row)
                states[score.test_fingerprint] = row
                adopted += 1
            else:
                row.observation_count = max(
                    int(row.observation_count or 0),
                    int(getattr(score, "observation_count", 0) or 0),
                )

            confidence = getattr(score, "confidence", "none") or "none"
            if row.first_scored_at is None and confidence != "none":
                row.first_scored_at = getattr(score, "computed_at", None) or now
                row.first_scored_confidence = confidence
                closed += 1

    # Flush the adoptions first so they are stamped by the same pass that
    # created them, rather than looking unswept until tomorrow night.
    await db.flush()
    result = await db.execute(
        update(FlakyDetectionState)
        .where(FlakyDetectionState.project_id == project_id)
        .values(last_swept_at=now, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    await db.flush()
    return {
        "swept": int(getattr(result, "rowcount", 0) or 0),
        "adopted": adopted,
        "scored": closed,
    }


# ── The measurement ──────────────────────────────────────────────────────────


@dataclass
class DetectionTiming:
    project_id: str
    available: bool
    insufficient_data_reason: Optional[str] = None
    tracked: int = 0
    measurable: int = 0
    excluded_not_observed: int = 0
    scored: int = 0
    awaiting_evidence: int = 0
    latency_hours: dict[str, Optional[float]] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)
    cadence: dict[str, Any] = field(default_factory=dict)
    bottleneck: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "available": self.available,
            "insufficient_data_reason": self.insufficient_data_reason,
            "tracked_fingerprints": self.tracked,
            "measurable_fingerprints": self.measurable,
            "excluded_first_appearance_not_observed": self.excluded_not_observed,
            "scored_fingerprints": self.scored,
            "awaiting_evidence_floor": self.awaiting_evidence,
            "evidence_floor_observations": MIN_OBSERVATIONS,
            "detection_latency_hours": self.latency_hours,
            "coverage": self.coverage,
            "cadence": self.cadence,
            "bottleneck": self.bottleneck,
            "notes": self.notes,
        }


async def detection_timing(
    db: "AsyncSession", project_id: Any, *, window_days: int = 30
) -> dict[str, Any]:
    """How long this project waits to learn a test is flaky, measured.

    Reports ``available=False`` with a reason rather than an empty-looking zero
    when there is nothing to measure — the same contract as
    ``/metrics/flaky-readiness`` and ``/metrics/tia-readiness``.
    """
    from sqlalchemy import func, select

    from app.models.postgres import FlakyDetectionState, TestRun

    since = datetime.now(timezone.utc) - timedelta(days=max(1, window_days))
    run_count = int(
        (
            await db.execute(
                select(func.count(TestRun.id)).where(
                    TestRun.project_id == project_id, TestRun.created_at >= since
                )
            )
        ).scalar()
        or 0
    )
    runs_per_day = run_count / float(max(1, window_days))
    cadence = recommend_cadence(runs_per_day)

    rows = list(
        (
            await db.execute(
                select(FlakyDetectionState)
                .where(FlakyDetectionState.project_id == project_id)
                # Stalest first, so a capped read reports on the rows most
                # likely to be a problem rather than an arbitrary slice.
                .order_by(FlakyDetectionState.last_swept_at.asc().nullsfirst())
                .limit(MAX_TIMING_ROWS + 1)
            )
        ).scalars().all()
    )
    sampled = len(rows) > MAX_TIMING_ROWS
    if sampled:
        rows = rows[:MAX_TIMING_ROWS]
    if not rows:
        return DetectionTiming(
            project_id=str(project_id),
            available=False,
            insufficient_data_reason=(
                "no test fingerprints have been screened for this project yet"
            ),
            cadence=cadence.to_dict(),
        ).to_dict()

    measurable: list[float] = []
    excluded = scored = awaiting = 0
    for row in rows:
        if row.first_scored_at is not None:
            scored += 1
        else:
            awaiting += 1
        if not row.first_seen_is_exact:
            excluded += 1
            continue
        if row.first_scored_at is None or row.first_seen_at is None:
            continue
        delta = _aware(row.first_scored_at) - _aware(row.first_seen_at)
        # Clock skew and backfills can produce a negative interval. Dropping it
        # is right: a negative detection latency is not a fast detection.
        if delta.total_seconds() >= 0:
            measurable.append(delta.total_seconds() / 3600.0)

    stale_before = datetime.now(timezone.utc) - timedelta(
        minutes=SWEEP_INTERVAL_MINUTES * STALE_SWEEP_MULTIPLIER
    )
    swept_recently = sum(
        1
        for row in rows
        if row.last_swept_at is not None and _aware(row.last_swept_at) >= stale_before
    )

    # The wait a still-unscoreable test faces, at this project's arrival rate.
    remaining = [
        expected_minutes_to_floor(row.observation_count, runs_per_day)
        for row in rows
        if row.first_scored_at is None
    ]
    remaining_known = [v for v in remaining if v is not None]
    typical_wait = (
        sorted(remaining_known)[len(remaining_known) // 2] if remaining_known else None
    )
    bottleneck = dominant_term(cadence.screen_interval_minutes, typical_wait)

    notes = []
    if sampled:
        notes.append(
            f"Measured over the {MAX_TIMING_ROWS} stalest tracked fingerprints, not "
            "the whole corpus — this project has more than the endpoint reads."
        )
    notes += [
        "Latency counts only fingerprints whose first appearance was observed; "
        f"{excluded} predate screening and are excluded rather than counted as zero.",
        "Retention purges can delete the older runs that would mark a fingerprint "
        "as pre-existing, which would make it look newly-seen. Treat a sudden drop "
        "in latency after a purge as an artefact.",
    ]
    if bottleneck == DOMINATED_BY_RUN_FREQUENCY:
        notes.append(
            "Detection here is limited by how often tests run, not by how often "
            f"they are screened: a test needs {MIN_OBSERVATIONS} observations before "
            "any score is defensible, and at this project's measured rate that is "
            "the larger term. Shortening the screening interval would not speed "
            "detection up."
        )
    elif bottleneck == DOMINATED_BY_SWEEP_CADENCE:
        notes.append(
            "Tests here run often enough that the screening interval is the larger "
            "term, so shortening it would measurably speed detection up."
        )

    return DetectionTiming(
        project_id=str(project_id),
        available=True,
        tracked=len(rows),
        measurable=len(measurable),
        excluded_not_observed=excluded,
        scored=scored,
        awaiting_evidence=awaiting,
        latency_hours=percentiles(measurable),
        coverage={
            "swept_within_two_intervals": swept_recently,
            "tracked": len(rows),
            "share": round(swept_recently / len(rows), 4) if rows else None,
            "stale_after_minutes": SWEEP_INTERVAL_MINUTES * STALE_SWEEP_MULTIPLIER,
        },
        cadence=cadence.to_dict(),
        bottleneck=bottleneck,
        notes=notes,
    ).to_dict()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
