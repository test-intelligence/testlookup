"""Flake load — the operational budget that replaces a debt burndown.

Roadmap Phase 1 (P1-C).

## Why this is a load and not a backlog

Google's data is that the rate at which flaky tests are introduced roughly
matches the rate at which they are fixed, even under sustained investment; Meta
went further and treats every real test as flaky to some degree, shifting the
question from *is this test flaky* to *how flaky is it*. Flakiness is therefore
a **steady state to manage**, not a queue that empties.

That has a concrete product consequence: a "flaky debt remaining" chart trending
toward zero is a promise the world will not keep. It will sit near a floor
forever, and a number that never reaches its implied target teaches users the
tool is broken rather than that the target was wrong.

So this reports a **load**: what share of recent runs carried at least one
flaky-attributed failure. A load has no implied zero. It is read against a
budget the team chooses — like error budgets — and going up or down is
meaningful without any endpoint being "done".

## What counts

A run contributes to the numerator when at least one of its test cases shows
retry evidence (``retry_count > 0`` or ``is_flaky_run``). That is deliberately
the *observed* signal available today, not a modelled score — Phase 2 adds
scoring, and this metric must keep working (and keep meaning the same thing)
whether or not that lands.

Runs are the denominator rather than tests because the question a team budgets
against is "how often does a run carry flake noise", which is what costs a
human an investigation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import TestCase, TestRun

logger = structlog.get_logger("services.flake_load")

# Below this many runs the share is too jumpy to be worth showing: with 3 runs
# a single flaky run reads as 33%. Report it as insufficient instead.
MIN_RUNS_FOR_LOAD = 10


@dataclass(frozen=True)
class FlakeLoad:
    """Share of runs carrying flake noise, or an honest refusal."""

    project_id: Any
    window_days: int
    total_runs: int
    runs_with_flake_noise: int
    flake_load: Optional[float]
    insufficient_reason: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "window_days": self.window_days,
            "total_runs": self.total_runs,
            "runs_with_flake_noise": self.runs_with_flake_noise,
            "flake_load": self.flake_load,
            "insufficient_reason": self.insufficient_reason,
            # Stated so the number cannot be mistaken for a backlog.
            "framing": (
                "Share of runs carrying at least one retried test. This is an "
                "operational load to manage against a budget, not a debt that "
                "trends to zero — flaky-test insertion rate tracks the fix rate."
            ),
            "min_runs_for_load": MIN_RUNS_FOR_LOAD,
        }


def summarize_flake_load(
    total_runs: int,
    runs_with_flake_noise: int,
    *,
    project_id: Any,
    window_days: int,
    min_runs: int = MIN_RUNS_FOR_LOAD,
) -> FlakeLoad:
    """Turn two counts into a load, or an insufficient-data answer.

    Pure, so the thresholds and the clamping can be exercised without a DB.
    """
    total = max(0, int(total_runs or 0))
    noisy = max(0, int(runs_with_flake_noise or 0))
    # A subset can never exceed its superset; if a caller passes inconsistent
    # counts, clamp rather than emit a >100% share.
    noisy = min(noisy, total)

    if total < min_runs:
        return FlakeLoad(
            project_id=project_id,
            window_days=window_days,
            total_runs=total,
            runs_with_flake_noise=noisy,
            flake_load=None,
            insufficient_reason=(
                f"only {total} run(s) in the last {window_days} days "
                f"({min_runs} required for a stable share)"
            ),
        )

    return FlakeLoad(
        project_id=project_id,
        window_days=window_days,
        total_runs=total,
        runs_with_flake_noise=noisy,
        flake_load=round(noisy / total, 4),
        insufficient_reason=None,
    )


async def get_flake_load(
    db: AsyncSession,
    project_id: Any,
    *,
    window_days: int = 30,
) -> dict[str, Any]:
    """Measure one project's flake load. Project-scoped by construction."""
    since = datetime.now(timezone.utc) - timedelta(days=window_days)

    total_runs = (
        await db.execute(
            select(func.count(TestRun.id)).where(
                TestRun.project_id == project_id,
                TestRun.created_at >= since,
            )
        )
    ).scalar_one_or_none() or 0

    noisy_runs = (
        await db.execute(
            select(func.count(distinct(TestCase.test_run_id)))
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(
                TestRun.project_id == project_id,
                TestRun.created_at >= since,
                or_(TestCase.retry_count > 0, TestCase.is_flaky_run.is_(True)),
            )
        )
    ).scalar_one_or_none() or 0

    return summarize_flake_load(
        total_runs, noisy_runs, project_id=project_id, window_days=window_days
    ).to_dict()
