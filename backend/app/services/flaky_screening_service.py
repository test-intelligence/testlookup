"""Tier 1: screen the tests most likely to be newly flaky, first.

Phase 6 of ``architecture/TEST_INTELLIGENCE_PLAN.md``.

## What justifies this, and what does not

The plan originally leaned on "75% of flaky tests are already flaky at their
introducing commit". That claim was **refuted 1–2** in the evidence review and
is not used here.

What survives is weaker and heavily qualified: **85/15 among order- and
implementation-dependent flaky tests in 55 Java OSS projects** — 85% were
catchable by screening tests that were new or directly modified, and the
remaining 15% became flaky through changes elsewhere. That corpus was 245 flaky
tests found by two detectors, skewed towards order- and
implementation-dependent flakiness, with async-wait, concurrency and network
flakiness under-sampled; the authors state the results may not generalize.

That is enough to justify the *shape* — look at the new and the
directly-modified first — and not enough to justify a constant. So nothing here
hard-codes 85, 15 or 150, and tier 1 never terminates the search: everything it
does not reach is still swept by tier 2, which is where the environment- and
dependency-induced flakiness this product actually sees would land.

## What screening can honestly conclude

Nothing, on its own. TestLookup ingests results; it does not execute tests, so
tier 1 cannot re-run a suspect ten times to see if it flips. A test observed
once is a test about which the only defensible statement is *"not yet
observable"* — and the whole point of Phase 6 is to say that promptly and
measurably, rather than to guess early.

So screening produces a **population and a reason**, not a verdict. The verdict
still comes from the score, once the evidence floor is cleared.

## "Directly modified" means directly modified

The overlap threshold is 1.0 — a same-subject filename stem match. The finding
this borrows its shape from is about tests whose own source was edited; a fuzzy
same-directory match is a different and much weaker claim, and letting it in
would quietly turn a screening tier into "most of the corpus".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger("services.flaky_screening")

# Why a fingerprint entered the screening population. Exported as a tuple so a
# consumer can iterate the vocabulary rather than hard-code a subset — the
# defect class that produced F-074, F-078 and UAT-002.
SCREEN_NEW_TEST = "new_test"
SCREEN_MODIFIED_TEST = "modified_test"
SCREEN_CORPUS_SWEEP = "corpus_sweep"
SCREEN_REASONS: tuple[str, ...] = (
    SCREEN_NEW_TEST,
    SCREEN_MODIFIED_TEST,
    SCREEN_CORPUS_SWEEP,
)

REASON_LABELS: dict[str, str] = {
    SCREEN_NEW_TEST: "new test",
    SCREEN_MODIFIED_TEST: "directly modified test",
    SCREEN_CORPUS_SWEEP: "background corpus sweep",
}

# A same-subject filename stem match. Deliberately the strictest rung of
# ``path_overlap`` — see the module docstring.
DIRECT_MODIFICATION_OVERLAP = 1.0

# Bounds. Tier 1 runs on a short beat, so it must stay cheap even on a busy
# project; a partial screen that says it was partial beats a worker timeout.
DEFAULT_WINDOW_HOURS = 6
MAX_SCREEN_ROWS = 50_000
MAX_SCREEN_CANDIDATES = 2_000


@dataclass
class ScreeningCandidate:
    """One fingerprint tier 1 looked at, and why.

    Carries no verdict by design: with a single observation the only honest
    statement is that the test is not yet observable.
    """

    test_fingerprint: str
    test_name: Optional[str]
    reason: str
    # Earliest surviving run for this fingerprint anywhere in the corpus.
    first_seen_at: datetime
    # True only when that earliest run is inside the screened window — i.e. we
    # watched it appear. False rows must not contribute to latency statistics.
    first_seen_is_exact: bool
    observation_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_fingerprint": self.test_fingerprint,
            "test_name": self.test_name,
            "reason": self.reason,
            "reason_label": REASON_LABELS.get(self.reason, self.reason),
            "first_seen_at": self.first_seen_at.isoformat(),
            "first_seen_is_exact": self.first_seen_is_exact,
            "observation_count": self.observation_count,
        }


def classify_reason(*, is_new: bool, is_modified: bool) -> Optional[str]:
    """Which tier-1 population a fingerprint belongs to.

    A test that is both new and touched by the change is reported as new: that
    is the stronger and more specific statement, and a new test's own file
    appearing in the diff is nearly tautological.

    Returns ``None`` for neither — tier 1 declines to claim a population rather
    than inventing one, and tier 2 still reaches it.
    """
    if is_new:
        return SCREEN_NEW_TEST
    if is_modified:
        return SCREEN_MODIFIED_TEST
    return None


def is_directly_modified(overlap: Optional[float]) -> bool:
    """Only a same-subject match counts as "directly modified"."""
    if overlap is None:
        return False
    try:
        return float(overlap) >= DIRECT_MODIFICATION_OVERLAP
    except (TypeError, ValueError):
        return False


async def screen_project(
    db: "AsyncSession",
    project_id: Any,
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    max_rows: int = MAX_SCREEN_ROWS,
    max_candidates: int = MAX_SCREEN_CANDIDATES,
) -> list[ScreeningCandidate]:
    """Tier-1 population for one project's recent window.

    Project-scoped by construction: ``test_fingerprint`` is unique only within
    a project, so an unscoped read would blend tenants.

    Read-only. Persistence and transaction ownership belong to the caller.
    """
    from sqlalchemy import func, select

    from app.models.postgres import RunCommitRange, TestCase, TestRun

    window_start = datetime.now(timezone.utc) - timedelta(hours=max(1, window_hours))

    rows = (
        await db.execute(
            select(TestCase, TestRun.id)
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(TestRun.project_id == project_id, TestRun.created_at >= window_start)
            .order_by(TestRun.created_at.desc())
            .limit(max_rows + 1)
        )
    ).all()
    truncated = len(rows) > max_rows
    if truncated:
        rows = rows[:max_rows]
        logger.warning(
            "flaky_screening_window_truncated",
            project_id=str(project_id),
            max_rows=max_rows,
            window_hours=window_hours,
        )

    # One representative case per fingerprint — the locator only needs the
    # test's identity, and building 50k locators would be the expensive part.
    representative: dict[str, Any] = {}
    run_ids: set[Any] = set()
    for case, run_id in rows:
        fingerprint = getattr(case, "test_fingerprint", None)
        if not isinstance(fingerprint, str) or not fingerprint:
            continue
        representative.setdefault(fingerprint, case)
        run_ids.add(run_id)

    if not representative:
        return []

    fingerprints = list(representative)[:max_candidates]
    if len(representative) > max_candidates:
        logger.warning(
            "flaky_screening_candidates_capped",
            project_id=str(project_id),
            seen=len(representative),
            max_candidates=max_candidates,
        )

    # Earliest surviving run per fingerprint, across the WHOLE corpus — not the
    # window. A window-local minimum would call every fingerprint new every
    # time the window rolled forward.
    #
    # Observations are counted as DISTINCT RUNS, not per-test rows. A retry
    # writes several rows for one execution, and counting those would inflate a
    # fingerprint past the evidence floor without any new evidence behind it —
    # the exact fabricated-confidence failure this roadmap keeps refusing.
    earliest_rows = (
        await db.execute(
            select(
                TestCase.test_fingerprint,
                func.min(TestRun.created_at),
                func.count(func.distinct(TestRun.id)),
            )
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(
                TestRun.project_id == project_id,
                TestCase.test_fingerprint.in_(fingerprints),
            )
            .group_by(TestCase.test_fingerprint)
        )
    ).all()
    earliest: dict[str, tuple[datetime, int]] = {
        fp: (seen_at, int(count or 0))
        for fp, seen_at, count in earliest_rows
        if isinstance(fp, str) and seen_at is not None
    }

    # Files changed by the runs in this window, unioned. The window is hours,
    # so "changed recently" and "changed by this run" are close enough to be
    # the same statement — and the alternative, per-run screening, would
    # re-screen the same fingerprint once per run for no extra information.
    changed_files: list[str] = []
    if run_ids:
        commit_rows = (
            await db.execute(
                select(RunCommitRange).where(RunCommitRange.run_id.in_(list(run_ids)))
            )
        ).scalars().all()
        for row in commit_rows:
            for commit in (getattr(row, "commits", None) or []):
                if not isinstance(commit, dict):
                    continue
                for path in (commit.get("files") or []):
                    if isinstance(path, str) and path:
                        changed_files.append(path)
    changed_files = list(dict.fromkeys(changed_files))

    candidates: list[ScreeningCandidate] = []
    for fingerprint in fingerprints:
        seen = earliest.get(fingerprint)
        if seen is None:
            continue
        first_seen_at, observation_count = seen
        if first_seen_at.tzinfo is None:
            first_seen_at = first_seen_at.replace(tzinfo=timezone.utc)
        is_new = first_seen_at >= window_start
        is_modified = False
        if changed_files and not is_new:
            # Only worth computing when it could change the answer.
            is_modified = _touches(representative[fingerprint], changed_files)
        reason = classify_reason(is_new=is_new, is_modified=is_modified)
        if reason is None:
            continue
        candidates.append(
            ScreeningCandidate(
                test_fingerprint=fingerprint,
                test_name=getattr(representative[fingerprint], "test_name", None),
                reason=reason,
                first_seen_at=first_seen_at,
                first_seen_is_exact=is_new,
                observation_count=observation_count,
            )
        )
    return candidates


def _touches(case: Any, changed_files: list[str]) -> bool:
    """Was this test's own source directly edited?

    Degrades to ``False`` rather than raising: a screening tier that fails
    closed still leaves tier 2 covering the fingerprint, whereas one that
    raises takes the whole beat down with it.
    """
    try:
        from app.services.commit_attribution_service import build_locator, path_overlap

        locator = build_locator([case])
        return any(
            is_directly_modified(path_overlap(locator, path)) for path in changed_files
        )
    except Exception:  # noqa: BLE001 — screening must degrade, never fail
        logger.warning("flaky_screening_locator_failed")
        return False
