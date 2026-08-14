"""Does this project have enough history for a windowed flakiness posterior?

Phase 0 (P0-3) of ``architecture/TEST_INTELLIGENCE_PLAN.md``, and the gate on
Phase 2.

## The question this answers

Every quantitative datapoint behind the flakiness roadmap comes from hyperscale
monorepos — Google, Atlassian, GitHub, Meta — or from Java OSS corpora where
academic per-test flake rates run about an order of magnitude lower. TestLookup
is a local-first, self-hosted product whose users are frequently small teams.

A Bayesian score over a moving window needs *runs per fingerprint* to have
anything to update a prior with. A fingerprint seen three times cannot produce a
calibrated posterior; it can only produce a confident-looking number that is
mostly prior. Building that scorer before checking whether the data supports it
is how a product ends up shipping fabricated confidence.

So this measures, per project: how many fingerprints clear a per-fingerprint run
threshold, the median runs per fingerprint, and the share of the corpus with
enough history. The verdict is a plain boolean plus the reason.

## Honesty contract

Mirrors ``get_tia_readiness``: when the corpus is too thin the answer is
``available: false`` with a concrete ``insufficient_data_reason``, never a
number dressed up as a measurement.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import TestCase, TestRun

logger = structlog.get_logger("services.flaky_readiness")

# A fingerprint needs at least this many runs in the window before a windowed
# posterior is meaningfully data-driven rather than prior-dominated.
MIN_RUNS_PER_FINGERPRINT = 20

# And the project needs at least this many such fingerprints for a per-project
# scorer to be worth running at all.
MIN_QUALIFYING_FINGERPRINTS = 25


@dataclass(frozen=True)
class FlakyReadiness:
    """Per-project verdict on whether scoring has data to work with."""

    project_id: Any
    window_days: int
    total_fingerprints: int
    qualifying_fingerprints: int
    median_runs_per_fingerprint: float
    max_runs_per_fingerprint: int
    qualifying_share: float
    available: bool
    insufficient_data_reason: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "window_days": self.window_days,
            "available": self.available,
            "insufficient_data_reason": self.insufficient_data_reason,
            "total_fingerprints": self.total_fingerprints,
            "qualifying_fingerprints": self.qualifying_fingerprints,
            "qualifying_share": self.qualifying_share,
            "median_runs_per_fingerprint": self.median_runs_per_fingerprint,
            "max_runs_per_fingerprint": self.max_runs_per_fingerprint,
            "thresholds": {
                "min_runs_per_fingerprint": MIN_RUNS_PER_FINGERPRINT,
                "min_qualifying_fingerprints": MIN_QUALIFYING_FINGERPRINTS,
            },
        }


def summarize_readiness(
    run_counts: list[int],
    *,
    project_id: Any,
    window_days: int,
    min_runs: int = MIN_RUNS_PER_FINGERPRINT,
    min_qualifying: int = MIN_QUALIFYING_FINGERPRINTS,
) -> FlakyReadiness:
    """Turn per-fingerprint run counts into a verdict.

    Pure, so the thresholds can be exercised exhaustively without a database.
    ``run_counts`` is one integer per distinct fingerprint in the window.
    """
    counts = [int(c) for c in (run_counts or []) if isinstance(c, (int, float)) and c > 0]
    total = len(counts)

    if total == 0:
        return FlakyReadiness(
            project_id=project_id,
            window_days=window_days,
            total_fingerprints=0,
            qualifying_fingerprints=0,
            median_runs_per_fingerprint=0.0,
            max_runs_per_fingerprint=0,
            qualifying_share=0.0,
            available=False,
            insufficient_data_reason=(
                f"no test results in the last {window_days} days"
            ),
        )

    qualifying = sum(1 for c in counts if c >= min_runs)
    med = float(median(counts))
    share = qualifying / total

    if qualifying < min_qualifying:
        reason = (
            f"only {qualifying} fingerprint(s) have >= {min_runs} runs in the last "
            f"{window_days} days ({min_qualifying} required); median is "
            f"{med:g} runs per test"
        )
        available = False
    else:
        reason = None
        available = True

    return FlakyReadiness(
        project_id=project_id,
        window_days=window_days,
        total_fingerprints=total,
        qualifying_fingerprints=qualifying,
        median_runs_per_fingerprint=med,
        max_runs_per_fingerprint=max(counts),
        qualifying_share=round(share, 4),
        available=available,
        insufficient_data_reason=reason,
    )


async def get_flaky_readiness(
    db: AsyncSession,
    project_id: Any,
    *,
    window_days: int = 90,
) -> dict[str, Any]:
    """Measure one project's corpus depth. Project-scoped by construction."""
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    rows = (
        await db.execute(
            # DISTINCT test_run_id, not count(*): the metric is runs per
            # fingerprint. A parameterised test, a retry, or the same name
            # appearing twice in one run would otherwise inflate the count and
            # let a shallow project pass the gate on volume it does not have.
            select(TestCase.test_fingerprint, func.count(func.distinct(TestCase.test_run_id)))
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(
                TestRun.project_id == project_id,
                TestRun.created_at >= since,
                TestCase.test_fingerprint.isnot(None),
            )
            .group_by(TestCase.test_fingerprint)
        )
    ).all()
    counts = [int(count) for _fingerprint, count in rows]
    return summarize_readiness(
        counts, project_id=project_id, window_days=window_days
    ).to_dict()
