"""Backtest the flaky classifier we already ship, per project.

Phase 0 (P0-2) of ``architecture/TEST_INTELLIGENCE_PLAN.md``. **Measures only —
nothing in the product consumes the result yet.**

## Why per project

Today a failure can be called flaky by matching its error signature against
signatures seen on previously-flaky failures. Alshammari et al. (ICST 2024)
measured exactly that technique over 230,439 failures in 22 Java projects and
found specificity ranging from 100% on some projects to **no better than random
on others** — the driver being whether a project's failures carry distinctive
exception types (``UnknownHostException`` matches well; a project drowning in
bare ``AssertionError`` collapses). So "does our classifier work?" has no global
answer, only a per-project one, and this module produces it.

## Why specificity, not accuracy

The expensive mistake is calling a REAL failure flaky and waving it through.
Specificity — of the failures we did not call flaky, how many truly were not —
is the metric that tracks that risk. Accuracy would let a project with mostly
non-flaky failures look excellent while still mislabelling the few that matter.

## Ground truth

A fingerprint is treated as genuinely flaky in the window when it both passed
and failed **on the same commit** — the standard, near-zero-false-positive
definition. Fingerprints seen on only one commit, or with a single outcome, are
not evidence either way and are excluded rather than assumed negative. That
exclusion is why ``sample_count`` is reported alongside every number: a
specificity computed from nine failures is not a measurement, and callers must
be able to see that.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import FlakyClassifierCalibration, TestCase, TestRun
from app.services.flaky_signals import error_signature

logger = structlog.get_logger("services.flaky_classifier_calibration")

# The strategy this module measures — the one the product already uses.
METHOD_ERROR_SIGNATURE = "error_signature_match"

# Below this many evaluable failures the answer is "insufficient", not a
# number. Deliberately conservative: a specificity from a handful of samples
# invites exactly the false confidence this whole exercise exists to prevent.
MIN_SAMPLES = 30

_FAILED = {"FAILED", "BROKEN"}


@dataclass(frozen=True)
class CalibrationResult:
    """Measured classifier quality for one project, or an honest refusal."""

    project_id: Any
    method: str
    specificity: Optional[float]
    sample_count: int
    true_negatives: int
    false_positives: int
    insufficient_reason: Optional[str]
    window_days: int

    @property
    def is_measured(self) -> bool:
        return self.specificity is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "method": self.method,
            "specificity": self.specificity,
            "sample_count": self.sample_count,
            "true_negatives": self.true_negatives,
            "false_positives": self.false_positives,
            "insufficient_reason": self.insufficient_reason,
            "window_days": self.window_days,
        }


def _status_of(row: Any) -> str:
    value = getattr(row, "status", None)
    raw = getattr(value, "value", value)
    return str(raw or "").strip().upper()


def compute_calibration(
    rows: Iterable[Any],
    *,
    project_id: Any,
    window_days: int = 90,
    min_samples: int = MIN_SAMPLES,
) -> CalibrationResult:
    """Compute specificity from ``(fingerprint, commit, status, error)`` rows.

    Pure and never raises — a malformed row is skipped, not fatal. Split from
    the DB read so it can be tested exhaustively without a database.

    Ground truth: a fingerprint is flaky if it both passed and failed on the
    same commit. Prediction: a failure is "called flaky" if its error signature
    was ever seen on a genuinely-flaky failure of that same fingerprint.
    """
    # (fingerprint, commit) -> set of statuses, and per fingerprint the
    # signatures observed on failures.
    outcomes: dict[tuple[str, str], set[str]] = defaultdict(set)
    failures: list[tuple[str, str, str]] = []  # (fingerprint, commit, signature)

    for row in rows or []:
        fingerprint = getattr(row, "test_fingerprint", None)
        commit = getattr(row, "commit_hash", None)
        if not isinstance(fingerprint, str) or not fingerprint:
            continue
        if not isinstance(commit, str) or not commit:
            # No commit anchor → cannot establish same-code flakiness.
            continue
        status = _status_of(row)
        if not status:
            continue
        outcomes[(fingerprint, commit)].add(status)
        if status in _FAILED:
            failures.append(
                (fingerprint, commit, error_signature(getattr(row, "error_message", None)))
            )

    # Fingerprints proven flaky: passed AND failed on the same commit.
    flaky_fingerprints: set[str] = set()
    for (fingerprint, _commit), statuses in outcomes.items():
        if statuses & _FAILED and any(s not in _FAILED for s in statuses):
            flaky_fingerprints.add(fingerprint)

    # Signatures the classifier would learn as "this means flaky".
    flaky_signatures: dict[str, set[str]] = defaultdict(set)
    for fingerprint, _commit, signature in failures:
        if fingerprint in flaky_fingerprints and signature:
            flaky_signatures[fingerprint].add(signature)

    # Evaluate only failures of fingerprints that are NOT flaky — those are the
    # true negatives specificity is about. A prediction of "flaky" there is a
    # false positive: a real failure the product would wave through.
    true_negatives = 0
    false_positives = 0
    for fingerprint, _commit, signature in failures:
        if fingerprint in flaky_fingerprints:
            continue
        predicted_flaky = any(
            signature and signature in sigs
            for other, sigs in flaky_signatures.items()
            if other != fingerprint
        )
        if predicted_flaky:
            false_positives += 1
        else:
            true_negatives += 1

    sample_count = true_negatives + false_positives
    if sample_count < min_samples:
        return CalibrationResult(
            project_id=project_id,
            method=METHOD_ERROR_SIGNATURE,
            specificity=None,
            sample_count=sample_count,
            true_negatives=true_negatives,
            false_positives=false_positives,
            insufficient_reason=(
                f"only {sample_count} evaluable non-flaky failures in the last "
                f"{window_days} days ({min_samples} required)"
            ),
            window_days=window_days,
        )

    return CalibrationResult(
        project_id=project_id,
        method=METHOD_ERROR_SIGNATURE,
        specificity=true_negatives / sample_count,
        sample_count=sample_count,
        true_negatives=true_negatives,
        false_positives=false_positives,
        insufficient_reason=None,
        window_days=window_days,
    )


async def calibrate_project(
    db: AsyncSession,
    project_id: Any,
    *,
    window_days: int = 90,
) -> CalibrationResult:
    """Read one project's recent failures and compute its calibration.

    Project-scoped by construction: ``test_fingerprint`` is not globally
    unique, so an unscoped read would blend tenants.
    """
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    rows = (
        await db.execute(
            select(
                TestCase.test_fingerprint,
                TestRun.commit_hash,
                TestCase.status,
                TestCase.error_message,
            )
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(
                TestRun.project_id == project_id,
                TestRun.created_at >= since,
                TestRun.commit_hash.isnot(None),
            )
        )
    ).all()
    return compute_calibration(rows, project_id=project_id, window_days=window_days)


async def store_calibration(db: AsyncSession, result: CalibrationResult) -> None:
    """Upsert the measured result.

    Staged only — the caller owns the commit (transaction-boundary discipline).
    """
    existing = (
        await db.execute(
            select(FlakyClassifierCalibration).where(
                FlakyClassifierCalibration.project_id == result.project_id,
                FlakyClassifierCalibration.method == result.method,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = FlakyClassifierCalibration(
            project_id=result.project_id, method=result.method,
        )
        db.add(existing)

    existing.specificity = result.specificity
    existing.sample_count = result.sample_count
    existing.true_negatives = result.true_negatives
    existing.false_positives = result.false_positives
    existing.insufficient_reason = result.insufficient_reason
    existing.window_days = result.window_days
    existing.computed_at = datetime.now(timezone.utc)
    await db.flush()
