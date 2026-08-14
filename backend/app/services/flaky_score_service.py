"""Continuous, decomposable flakiness score.

Phase 2 (P2-B) of ``architecture/TEST_INTELLIGENCE_PLAN.md``.

## What this replaces

A binary flaky/not-flaky label answers "is it?", which is the wrong question:
all real tests are flaky to some degree, so the useful question is *how* flaky,
and *on what evidence*. This fuses four signals into a bounded 0–1 score and
stores every component beside it.

## Why there is no Bayesian posterior here

The plan originally specified one. The Phase 0 census then measured the actual
corpus: every genuine project on the reference deployment carries 12–15 test
fingerprints with a median of 5–12 runs each, against thresholds wanting ≥25
fingerprints at ≥20 runs. A moving-window posterior over 5–12 observations is
dominated by its prior — it emits a confident-looking number that is mostly an
assumption. The four signals below degrade honestly instead: with little data
they produce small, low-confidence values rather than a fabricated certainty.

That decision is recorded in the plan with its evidence and its limits. It is a
measurement, not a permanent ruling.

## The signals

======================  ==========================================  ==========
signal                  meaning                                     source
======================  ==========================================  ==========
result_volatility       pass/fail flips per adjacent run pair       have
retry_rate              share of runs needing an in-run retry       have
duration_variance       coefficient of variation of runtime         perf_baselines
environment_instability outcome disagrees across environments       new (P0-1)
======================  ==========================================  ==========

Each is already a 0–1 rate, so fusion is a weighted mean — deliberately the
dullest possible combiner. Anything cleverer would be unfalsifiable at this data
volume, and a weighted mean can be explained to the person whose test it
just scored.

## Confidence is not folded into the score

A test seen 4 times and one seen 400 can both produce 0.5. Collapsing that is
exactly how a thin-history guess comes to look like a measurement, so
``confidence`` is a separate band derived only from observation count, and a
score below the evidence floor is reported with ``confidence="none"``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Optional

import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger("services.flaky_score")

# Weights are stored per row, so changing them here does not silently
# reinterpret history. They sum to 1.0 so the composite stays in [0, 1].
#
# Result volatility carries the most weight because a pass/fail flip on
# unchanged code is the closest thing to direct evidence of flakiness; the other
# three are corroborating rather than diagnostic on their own. A slow test is
# not a flaky test, which is why duration carries the least.
DEFAULT_WEIGHTS: dict[str, float] = {
    "result_volatility": 0.45,
    "retry_rate": 0.25,
    "environment_instability": 0.20,
    "duration_variance": 0.10,
}

# Observation thresholds for the confidence band. Chosen to be legible rather
# than derived — and published in the payload so they can be argued with.
CONFIDENCE_NONE = "none"
CONFIDENCE_LOW = "low"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_HIGH = "high"

MIN_OBSERVATIONS = 5        # below this the score is not reported at all
LOW_CONFIDENCE_MAX = 9
MEDIUM_CONFIDENCE_MAX = 19  # 20+ observations reads as high

# A coefficient of variation at or above this is treated as fully unstable
# runtime. Above ~1.0 the standard deviation exceeds the mean, which is already
# an extreme spread; clamping avoids one pathological run dominating the score.
DURATION_CV_CEILING = 1.0

# Hard ceiling on rows read per project per scoring pass. The sweep visits every
# active project, so an unbounded read is a worker-memory risk, not a
# theoretical one.
MAX_SCORING_ROWS = 200_000


def _clamp(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    """Coerce to a float in [low, high]; non-numeric becomes ``low``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return low
    if number != number:  # NaN
        return low
    return max(low, min(high, number))


def confidence_for(observation_count: int) -> str:
    """Band the confidence from evidence volume alone.

    Deliberately independent of the score: how *much* we looked and what we
    *saw* are different facts, and merging them hides the first one.
    """
    count = max(0, int(observation_count or 0))
    if count < MIN_OBSERVATIONS:
        return CONFIDENCE_NONE
    if count <= LOW_CONFIDENCE_MAX:
        return CONFIDENCE_LOW
    if count <= MEDIUM_CONFIDENCE_MAX:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_HIGH


def duration_variance_signal(mean_ms: Any, stddev_ms: Any) -> float:
    """Coefficient of variation, clamped to [0, 1].

    Reuses the Welford statistics ``perf_baselines`` already maintains per
    (project, fingerprint) — recomputing them would be both wasteful and a
    second source of truth. Uses CV rather than raw stddev so a 10-second test
    varying by a second is not scored like a 100ms test varying by a second.
    """
    mean = _clamp(mean_ms, 0.0, float("inf"))
    stddev = _clamp(stddev_ms, 0.0, float("inf"))
    if mean <= 0:
        # No usable baseline — contribute nothing rather than inventing spread.
        return 0.0
    return _clamp((stddev / mean) / DURATION_CV_CEILING)


def environment_instability_signal(
    outcomes_by_environment: Mapping[str, Iterable[bool]],
) -> float:
    """How much the outcome disagrees ACROSS environments.

    Returns the share of environments whose pass rate differs from the overall
    pass rate — a test that fails only on one runner profile is a different
    animal from one that fails everywhere.

    Environments that were never recorded must be excluded by the caller: an
    unknown environment is not a group (see ``run_environment``). With fewer
    than two known environments there is nothing to compare, so this is 0.0 —
    an absence of evidence, never an assertion of stability.
    """
    per_env: list[float] = []
    total_pass = total_runs = 0
    for _env, outcomes in (outcomes_by_environment or {}).items():
        results = [bool(o) for o in (outcomes or [])]
        if not results:
            continue
        passed = sum(1 for r in results if r)
        per_env.append(passed / len(results))
        total_pass += passed
        total_runs += len(results)

    if len(per_env) < 2 or total_runs == 0:
        return 0.0

    overall = total_pass / total_runs
    # Mean absolute deviation from the overall rate, scaled so that a perfectly
    # split corpus (half the environments always pass, half always fail) is 1.0.
    deviation = sum(abs(rate - overall) for rate in per_env) / len(per_env)
    return _clamp(deviation * 2)


@dataclass(frozen=True)
class FlakinessScore:
    """A decomposable score, or an honest refusal to score."""

    test_fingerprint: str
    score: Optional[float]
    # Human-readable name, carried alongside the fingerprint so a score can be
    # shown without a second lookup. Optional: a fingerprint is the identity,
    # a name is a convenience.
    test_name: Optional[str] = None
    components: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    observation_count: int = 0
    confidence: str = CONFIDENCE_NONE
    insufficient_reason: Optional[str] = None

    @property
    def is_scored(self) -> bool:
        return self.score is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_fingerprint": self.test_fingerprint,
            "test_name": self.test_name,
            "score": self.score,
            "components": dict(self.components),
            "weights": dict(self.weights),
            "observation_count": self.observation_count,
            "confidence": self.confidence,
            "insufficient_reason": self.insufficient_reason,
            "thresholds": {
                "min_observations": MIN_OBSERVATIONS,
                "low_confidence_max": LOW_CONFIDENCE_MAX,
                "medium_confidence_max": MEDIUM_CONFIDENCE_MAX,
            },
        }


def compute_score(
    *,
    test_fingerprint: str,
    observation_count: int,
    test_name: Optional[str] = None,
    result_volatility: Any = 0.0,
    retry_rate: Any = 0.0,
    duration_variance: Any = 0.0,
    environment_instability: Any = 0.0,
    weights: Optional[Mapping[str, float]] = None,
) -> FlakinessScore:
    """Fuse the four signals into a bounded, decomposable score.

    Pure and never raises: a malformed signal clamps to 0 rather than poisoning
    the composite. Below ``MIN_OBSERVATIONS`` no score is produced at all —
    the components are still returned so a caller can see what little was
    observed, but ``score`` is ``None``.
    """
    active_weights = dict(weights or DEFAULT_WEIGHTS)
    components = {
        "result_volatility": _clamp(result_volatility),
        "retry_rate": _clamp(retry_rate),
        "environment_instability": _clamp(environment_instability),
        "duration_variance": _clamp(duration_variance),
    }
    count = max(0, int(observation_count or 0))

    if count < MIN_OBSERVATIONS:
        return FlakinessScore(
            test_fingerprint=test_fingerprint,
            test_name=test_name,
            score=None,
            components=components,
            weights=active_weights,
            observation_count=count,
            confidence=CONFIDENCE_NONE,
            insufficient_reason=(
                f"only {count} observation(s); {MIN_OBSERVATIONS} required "
                "before a score is meaningful"
            ),
        )

    weight_total = sum(active_weights.get(name, 0.0) for name in components)
    if weight_total <= 0:
        # A caller passing all-zero weights gets 0.0, not a ZeroDivisionError.
        composite = 0.0
    else:
        composite = sum(
            value * active_weights.get(name, 0.0) for name, value in components.items()
        ) / weight_total

    return FlakinessScore(
        test_fingerprint=test_fingerprint,
        test_name=test_name,
        score=round(_clamp(composite), 4),
        components=components,
        weights=active_weights,
        observation_count=count,
        confidence=confidence_for(count),
        insufficient_reason=None,
    )


# ── Persistence ─────────────────────────────────────────────────────────────
#
# Split from the pure functions above so the maths stays exhaustively testable
# without a database, and so a caller can score a fingerprint from signals it
# already has in memory without a round trip.


async def score_project(
    db: "AsyncSession",
    project_id: Any,
    *,
    window_days: int = 30,
    limit: int = 500,
    max_rows: int = MAX_SCORING_ROWS,
) -> list[FlakinessScore]:
    """Score every fingerprint in a project's recent window.

    Project-scoped by construction — ``test_fingerprint`` is not globally
    unique, so an unscoped read would blend tenants.

    Reads three of the four signals from the per-run rows and the fourth
    (duration variance) from the ``perf_baselines`` Welford statistics that are
    already maintained, rather than recomputing a second source of truth.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.models.postgres import PerformanceBaseline, TestCase, TestRun
    from app.services.flaky_signals import compute_intermittency_signals
    from app.services.run_environment import resolve_environment

    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    # BOUNDED. This runs as a nightly sweep over every active project, and a
    # busy project's 30-day window can be hundreds of thousands of per-test
    # rows — enough to exhaust a worker. Cap the read and report when the cap
    # bit, rather than silently scoring a partial window as if it were whole.
    rows = (
        await db.execute(
            select(TestCase, TestRun)
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(TestRun.project_id == project_id, TestRun.created_at >= since)
            .order_by(TestRun.created_at.desc())
            .limit(max_rows + 1)
        )
    ).all()
    truncated = len(rows) > max_rows
    if truncated:
        rows = rows[:max_rows]
        logger.warning(
            "flaky_score_window_truncated",
            project_id=str(project_id),
            max_rows=max_rows,
            window_days=window_days,
        )
    # Read newest-first so the cap keeps RECENT history (what a stability
    # judgement is made from), then restore run order for flip adjacency.
    rows = list(reversed(rows))

    # Group per fingerprint, preserving run order (flip counting is adjacency).
    per_fingerprint: dict[str, list[tuple[Any, Any]]] = {}
    for case, run in rows:
        fingerprint = getattr(case, "test_fingerprint", None)
        if isinstance(fingerprint, str) and fingerprint:
            per_fingerprint.setdefault(fingerprint, []).append((case, run))

    baselines = {
        row.test_fingerprint: row
        for row in (
            await db.execute(
                select(PerformanceBaseline).where(
                    PerformanceBaseline.project_id == project_id
                )
            )
        ).scalars().all()
    }

    scored: list[FlakinessScore] = []
    for fingerprint, pairs in list(per_fingerprint.items())[:limit]:
        signals = compute_intermittency_signals(
            [
                {
                    "status": getattr(case, "status", None),
                    "error_message": getattr(case, "error_message", None),
                    "stack_trace": getattr(case, "stack_trace", None),
                    "retry_count": getattr(case, "retry_count", None),
                    "is_flaky_run": getattr(case, "is_flaky_run", None),
                }
                for case, _run in pairs
            ]
        )

        # Outcomes bucketed by KNOWN environment only. An unrecorded
        # environment is not a group — folding those together would make a
        # corpus that never recorded environments look perfectly consistent.
        by_environment: dict[str, list[bool]] = {}
        for case, run in pairs:
            resolved = resolve_environment(run)
            if not resolved.is_known:
                continue
            status = str(getattr(getattr(case, "status", None), "value",
                                 getattr(case, "status", "")) or "").upper()
            by_environment.setdefault(resolved.key, []).append(
                status not in {"FAILED", "BROKEN"}
            )

        baseline = baselines.get(fingerprint)
        scored.append(
            compute_score(
                test_fingerprint=fingerprint,
                test_name=getattr(pairs[-1][0], "test_name", None),
                observation_count=len(pairs),
                result_volatility=signals.status_volatility,
                retry_rate=signals.in_run_retry_rate,
                duration_variance=duration_variance_signal(
                    getattr(baseline, "mean_ms", 0), getattr(baseline, "stddev_ms", 0)
                ),
                environment_instability=environment_instability_signal(by_environment),
            )
        )
    return scored


async def store_scores(
    db: "AsyncSession",
    project_id: Any,
    scores: Iterable[FlakinessScore],
    *,
    window_days: int = 30,
) -> int:
    """Upsert scored fingerprints. Staged only — the caller owns the commit.

    Unscored fingerprints (below the evidence floor) are skipped rather than
    written as 0.0: a stored zero would read as "measured, and clean".
    """
    from sqlalchemy import select

    from app.models.postgres import FlakyScore

    written = 0
    for result in scores:
        if not result.is_scored:
            continue
        existing = (
            await db.execute(
                select(FlakyScore).where(
                    FlakyScore.project_id == project_id,
                    FlakyScore.test_fingerprint == result.test_fingerprint,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = FlakyScore(
                project_id=project_id, test_fingerprint=result.test_fingerprint
            )
            db.add(existing)
        existing.score = result.score
        existing.components = dict(result.components)
        existing.weights = dict(result.weights)
        existing.observation_count = result.observation_count
        existing.confidence = result.confidence
        # Persist the window the score was actually computed over. Leaving the
        # column at its default would let a 90-day score claim it was 30 —
        # a stored provenance lie about the number beside it.
        existing.window_days = window_days
        if result.test_name:
            existing.test_name = result.test_name
        written += 1
    await db.flush()
    return written
