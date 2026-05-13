"""
Two-run compare — Tier 2 item 8.

Compares two ``TestRun`` rows and produces a per-test diff classified
by how the test's status changed between the left and right run. The
user picks the two runs in the UI; the classifier does the pairing.

Pairing uses ``TestCase.test_fingerprint`` — the stable hash of test
name + suite + package — so the same logical test matches across
builds even when its UUID changes.

Classifications:

* ``new_failure``   — left=passed,   right=failed/broken
* ``fixed``         — left=failed,   right=passed
* ``still_failing`` — left=failed,   right=failed
* ``regressed``     — left=passed,   right=skipped/broken (partial fail)
* ``improved``      — left=broken,   right=passed
* ``new_test``      — did not exist in left run
* ``removed_test``  — did not exist in right run
* ``duration_spike``— same status, right_duration > 3× left_duration

The response includes aggregate counts so the UI's summary tiles can
render without re-computing the classification client-side.
"""
from __future__ import annotations

import uuid
from difflib import SequenceMatcher
from typing import Any, Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import LaunchStatus, TestCase, TestRun

logger = structlog.get_logger("services.run_compare")


# Tests whose right-side duration is more than this multiple of the
# left-side duration count as duration_spikes — one of the release-gate
# signals planned in Tier 2 item 10.
_DURATION_SPIKE_MULTIPLIER = 3.0

# Hard cap on the number of per-test delta rows returned so a diff
# between two enormous runs doesn't DOS the frontend.
_MAX_DELTA_ROWS = 500

# Fuzzy-pair pass (carried-forward debt: "Run compare test pairing —
# stable fingerprint works, but tests that get renamed between runs
# don't pair. Consider a fuzzy match as a second pass."). Runs after
# the primary fingerprint match; only considers TestCases that landed
# as removed_test + new_test in the first pass.
_FUZZY_PAIR_THRESHOLD = 0.82

# Hard cap on how many unmatched rows we'll fuzzy-pair. The pairing is
# O(removed × added) so we bail out on huge runs rather than spending
# seconds in the matcher. Diff users looking at 500+ removed/added
# tests already have a bigger problem than rename tracking.
_FUZZY_PAIR_MAX_CANDIDATES = 500


def normalize_suite_name(suite_name: Optional[str]) -> str:
    """Canonical form for case-insensitive suite matching."""
    return (suite_name or "").strip().lower()


def _status_bucket(status: Optional[str]) -> str:
    """Collapse a TestStatus into ``passed`` | ``failed`` | ``skipped`` |
    ``broken`` | ``unknown`` for classification. Accepts None."""
    if not status:
        return "unknown"
    s = str(status).upper()
    if s in ("PASSED", "PASS"):
        return "passed"
    if s in ("FAILED", "FAIL"):
        return "failed"
    if s in ("BROKEN", "ERROR"):
        return "broken"
    if s in ("SKIPPED", "SKIP"):
        return "skipped"
    return "unknown"


def _classify(
    left_status: Optional[str],
    right_status: Optional[str],
    left_duration: Optional[int],
    right_duration: Optional[int],
) -> Optional[str]:
    """Return a classification label or ``None`` when there's no change
    worth surfacing (both sides identical pass + similar duration)."""
    if left_status is None and right_status is not None:
        return "new_test" if _status_bucket(right_status) == "passed" else "new_failure"
    if right_status is None and left_status is not None:
        return "removed_test"

    lb = _status_bucket(left_status)
    rb = _status_bucket(right_status)

    if lb == "passed" and rb in ("failed", "broken"):
        return "new_failure"
    if lb in ("failed", "broken") and rb == "passed":
        return "fixed"
    if lb == "failed" and rb == "failed":
        return "still_failing"
    if lb == "broken" and rb == "broken":
        return "still_failing"
    if lb == "passed" and rb == "skipped":
        return "regressed"
    if lb == "passed" and rb == "passed":
        # Same status — only surface on duration spike.
        if (
            left_duration is not None
            and right_duration is not None
            and left_duration > 0
            and right_duration >= left_duration * _DURATION_SPIKE_MULTIPLIER
        ):
            return "duration_spike"
        return None
    if lb == "skipped" and rb == "passed":
        return "fixed"
    if lb != rb:
        # Any other status change — surface as regressed for triage.
        return "regressed"
    return None


def _similarity(
    left_name: Optional[str],
    left_suite: Optional[str],
    right_name: Optional[str],
    right_suite: Optional[str],
) -> float:
    """Weighted similarity score for rename detection.

    The naive ``SequenceMatcher.ratio()`` alone is the wrong metric
    here: its denominator is ``len(a) + len(b)``, so a common refactor
    like ``test_login`` → ``test_login_with_valid_credentials`` scores
    ~0.58 even though the shorter name is a full prefix of the longer
    one. Pure containment (``longest_block / shorter``) is the mirror
    image — it returns 1.0 whenever one string is a substring of the
    other, which is too permissive for tie-breaking between candidates
    that all happen to share a short common prefix.

    We combine both: ``0.7 * containment + 0.3 * ratio``. Containment
    carries the rename signal for suffix/prefix additions; ratio acts
    as a tie-breaker that favours pairs whose overall length is
    closer to equal. Empirically this lets ``test_login`` →
    ``test_login_with_valid_credentials`` pair at ~0.84 while
    unrelated tests that share a ``test_`` prefix stay below 0.5.

    Cross-suite pairs return 0.0 outright. 99% of renames keep the
    test in the same suite, and cross-suite pairs are too ambiguous
    to auto-pair — if a customer ever needs cross-suite rename
    tracking, relax this guard and raise the threshold.
    """
    left_n = left_name or ""
    right_n = right_name or ""
    if not left_n or not right_n:
        return 0.0
    if normalize_suite_name(left_suite) != normalize_suite_name(right_suite):
        return 0.0

    matcher = SequenceMatcher(None, left_n, right_n)
    ratio = matcher.ratio()
    blocks = matcher.get_matching_blocks()
    longest_block = max((b.size for b in blocks), default=0)
    shorter = min(len(left_n), len(right_n))
    containment = (longest_block / shorter) if shorter else 0.0
    return 0.7 * containment + 0.3 * ratio


def _greedy_fuzzy_pair(
    removed: list[TestCase],
    added: list[TestCase],
) -> list[tuple[TestCase, TestCase, float]]:
    """Greedy best-match pairing between unmatched removed and added tests.

    Returns a list of ``(left_tc, right_tc, score)`` triples. Each
    TestCase appears at most once in the result. Pairs are picked
    highest-score-first so the strongest rename signal wins a contested
    target. Scores below ``_FUZZY_PAIR_THRESHOLD`` are not considered.

    Returns an empty list when either side is empty or either side
    exceeds ``_FUZZY_PAIR_MAX_CANDIDATES`` — at that scale the
    O(n × m) scoring is too expensive for an interactive endpoint and
    the user's mental model of "these are the renamed tests" breaks
    down anyway.
    """
    if not removed or not added:
        return []
    if len(removed) > _FUZZY_PAIR_MAX_CANDIDATES or len(added) > _FUZZY_PAIR_MAX_CANDIDATES:
        logger.info(
            "run_compare fuzzy pair skipped: too many candidates",
            removed=len(removed),
            added=len(added),
            cap=_FUZZY_PAIR_MAX_CANDIDATES,
        )
        return []

    candidates: list[tuple[float, TestCase, TestCase]] = []
    for r in removed:
        for a in added:
            score = _similarity(
                r.test_name, r.suite_name,
                a.test_name, a.suite_name,
            )
            if score >= _FUZZY_PAIR_THRESHOLD:
                candidates.append((score, r, a))

    candidates.sort(key=lambda t: -t[0])
    used_removed: set[str] = set()
    used_added: set[str] = set()
    pairs: list[tuple[TestCase, TestCase, float]] = []
    for score, r, a in candidates:
        r_fp = r.test_fingerprint or ""
        a_fp = a.test_fingerprint or ""
        if r_fp in used_removed or a_fp in used_added:
            continue
        used_removed.add(r_fp)
        used_added.add(a_fp)
        pairs.append((r, a, score))
    return pairs


async def _load_summary(db: AsyncSession, run_id: uuid.UUID) -> Optional[TestRun]:
    result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    return result.scalar_one_or_none()


async def _load_test_rows(
    db: AsyncSession,
    run_id: uuid.UUID,
    suite_name: Optional[str] = None,
) -> dict[str, TestCase]:
    """Return a map keyed by ``test_fingerprint``.

    Same-fingerprint duplicates inside a single run (e.g. retries) are
    resolved by keeping the last-observed row — matches how the release
    dashboards render the run.
    """
    stmt = select(TestCase).where(TestCase.test_run_id == run_id)
    suite_key = normalize_suite_name(suite_name)
    if suite_key:
        stmt = stmt.where(func.lower(func.trim(TestCase.suite_name)) == suite_key)
    result = await db.execute(stmt)
    rows: dict[str, TestCase] = {}
    for tc in result.scalars().all():
        fp = tc.test_fingerprint
        if not fp:
            continue
        rows[fp] = tc
    return rows


def _summary_dict(
    run: TestRun,
    scoped_tests: Optional[list[TestCase]] = None,
    suite_name: Optional[str] = None,
) -> dict[str, Any]:
    if scoped_tests is not None:
        total = len(scoped_tests)
        passed = sum(1 for tc in scoped_tests if _status_bucket(tc.status) == "passed")
        failed = sum(1 for tc in scoped_tests if _status_bucket(tc.status) == "failed")
        broken = sum(1 for tc in scoped_tests if _status_bucket(tc.status) == "broken")
        skipped = sum(1 for tc in scoped_tests if _status_bucket(tc.status) == "skipped")
        duration_ms = sum(int(tc.duration_ms or 0) for tc in scoped_tests)
        pass_rate = round((passed / total) * 100, 3) if total else 0.0
        display_suite = suite_name or (scoped_tests[0].suite_name if scoped_tests else None)
        return {
            "id": run.id,
            "project_id": run.project_id,
            "build_number": run.build_number,
            "branch": run.branch,
            "commit_hash": run.commit_hash,
            "status": run.status,
            "total_tests": total,
            "passed_tests": passed,
            "failed_tests": failed,
            "broken_tests": broken,
            "skipped_tests": skipped,
            "pass_rate": pass_rate,
            "duration_ms": duration_ms,
            "start_time": run.start_time,
            "end_time": run.end_time,
            "primary_suite_name": display_suite,
            "suite_names": [display_suite] if display_suite else [],
        }

    return {
        "id": run.id,
        "project_id": run.project_id,
        "build_number": run.build_number,
        "branch": run.branch,
        "commit_hash": run.commit_hash,
        "status": run.status,
        "total_tests": int(run.total_tests or 0),
        "passed_tests": int(run.passed_tests or 0),
        "failed_tests": int(run.failed_tests or 0),
        "broken_tests": int(run.broken_tests or 0),
        "skipped_tests": int(run.skipped_tests or 0),
        "pass_rate": float(run.pass_rate) if run.pass_rate is not None else None,
        "duration_ms": int(run.duration_ms) if run.duration_ms is not None else None,
        "start_time": run.start_time,
        "end_time": run.end_time,
        "primary_suite_name": run.primary_suite_name,
        "suite_names": run.suite_names or [],
    }


async def ensure_run_has_suite(
    db: AsyncSession,
    run_id: uuid.UUID,
    suite_name: str,
) -> bool:
    suite_key = normalize_suite_name(suite_name)
    if not suite_key:
        return False
    result = await db.execute(
        select(TestCase.id)
        .where(
            TestCase.test_run_id == run_id,
            func.lower(func.trim(TestCase.suite_name)) == suite_key,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def resolve_latest_suite_pair(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    suite_name: str,
) -> tuple[TestRun, TestRun]:
    """Return ``(previous, latest)`` completed runs for a suite on the latest branch.

    The latest run establishes the branch. The previous side is selected from
    the same branch by default so nightly branch comparisons do not silently
    cross streams.
    """
    suite_key = normalize_suite_name(suite_name)
    if not suite_key:
        raise ValueError("suite_name is required")

    suite_exists = (
        select(TestCase.id)
        .where(
            TestCase.test_run_id == TestRun.id,
            func.lower(func.trim(TestCase.suite_name)) == suite_key,
        )
        .exists()
    )
    latest_result = await db.execute(
        select(TestRun)
        .where(
            TestRun.project_id == project_id,
            TestRun.status != LaunchStatus.IN_PROGRESS,
            suite_exists,
        )
        .order_by(func.coalesce(TestRun.end_time, TestRun.created_at).desc())
        .limit(1)
    )
    latest = latest_result.scalar_one_or_none()
    if latest is None:
        raise LookupError(f"No completed runs found for suite {suite_name}")

    branch_filter = (
        TestRun.branch.is_(None)
        if latest.branch is None
        else TestRun.branch == latest.branch
    )
    previous_result = await db.execute(
        select(TestRun)
        .where(
            TestRun.project_id == project_id,
            TestRun.id != latest.id,
            TestRun.status != LaunchStatus.IN_PROGRESS,
            branch_filter,
            suite_exists,
        )
        .order_by(func.coalesce(TestRun.end_time, TestRun.created_at).desc())
        .limit(1)
    )
    previous = previous_result.scalar_one_or_none()
    if previous is None:
        branch_label = latest.branch or "no branch"
        raise LookupError(
            f"At least two completed runs are required to compare suite {suite_name} on branch {branch_label}"
        )
    return previous, latest


async def compare_runs(
    db: AsyncSession,
    left_id: uuid.UUID,
    right_id: uuid.UUID,
    *,
    suite_name: Optional[str] = None,
    selection: Optional[dict[str, Any]] = None,
    ai_report: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the compare document. Returns a plain dict suitable for
    passing straight into ``RunCompareResponse``.

    Caller is expected to have already enforced tenant isolation via
    ``resolve_project_scope`` on both runs' projects.

    Raises ``LookupError`` when either run is missing.
    """
    left_run = await _load_summary(db, left_id)
    right_run = await _load_summary(db, right_id)
    if left_run is None:
        raise LookupError(f"Left run {left_id} not found")
    if right_run is None:
        raise LookupError(f"Right run {right_id} not found")

    left_tests = await _load_test_rows(db, left_id, suite_name=suite_name)
    right_tests = await _load_test_rows(db, right_id, suite_name=suite_name)

    if suite_name and (not left_tests or not right_tests):
        missing = []
        if not left_tests:
            missing.append("left")
        if not right_tests:
            missing.append("right")
        raise LookupError(
            f"Suite {suite_name} was not found in the {' and '.join(missing)} run"
        )

    all_fingerprints = set(left_tests.keys()) | set(right_tests.keys())

    counts = {
        "new_failure": 0,
        "fixed": 0,
        "still_failing": 0,
        "regressed": 0,
        "improved": 0,
        "new_test": 0,
        "removed_test": 0,
        "duration_spike": 0,
        # Second-pass fuzzy matcher bucket: a test whose fingerprint
        # doesn't match across the two runs but whose ``suite::name``
        # is similar enough that it's almost certainly the same
        # logical test after a rename/refactor. Landed delta entries
        # also carry ``paired_by``, ``previous_test_name``, and
        # ``previous_test_fingerprint`` so the UI can render a "was:"
        # label alongside the current name.
        "renamed": 0,
    }

    deltas: list[dict[str, Any]] = []
    for fp in all_fingerprints:
        left_tc = left_tests.get(fp)
        right_tc = right_tests.get(fp)
        left_status = left_tc.status if left_tc else None
        right_status = right_tc.status if right_tc else None
        left_duration = left_tc.duration_ms if left_tc else None
        right_duration = right_tc.duration_ms if right_tc else None

        classification = _classify(
            left_status, right_status, left_duration, right_duration,
        )
        if classification is None:
            continue
        counts[classification] = counts.get(classification, 0) + 1

        # Prefer the right-side name/suite when available (the "target"
        # run reflects the current state).
        display_tc = right_tc or left_tc
        delta_duration = (
            (right_duration - left_duration)
            if right_duration is not None and left_duration is not None
            else None
        )
        deltas.append({
            "test_fingerprint": fp,
            "test_name": display_tc.test_name if display_tc else None,
            "suite_name": display_tc.suite_name if display_tc else None,
            "left_status": str(left_status) if left_status else None,
            "right_status": str(right_status) if right_status else None,
            "left_duration_ms": left_duration,
            "right_duration_ms": right_duration,
            "delta_duration_ms": delta_duration,
            "classification": classification,
            "paired_by": "fingerprint",
            "previous_test_name": None,
            "previous_test_fingerprint": None,
        })

    # Second pass — fuzzy-pair the leftover removed_test + new_test
    # buckets so renamed tests stop landing as "gone + appeared". The
    # matcher only considers TestCases whose first-pass classification
    # was removed_test or new_test; everything else is already paired.
    fuzzy_removed = [
        left_tests[d["test_fingerprint"]]
        for d in deltas
        if d["classification"] == "removed_test"
    ]
    fuzzy_added = [
        right_tests[d["test_fingerprint"]]
        for d in deltas
        if d["classification"] == "new_test"
    ]
    fuzzy_pairs = _greedy_fuzzy_pair(fuzzy_removed, fuzzy_added)

    if fuzzy_pairs:
        paired_removed_fps = {r.test_fingerprint for r, _, _ in fuzzy_pairs}
        paired_added_fps = {a.test_fingerprint for _, a, _ in fuzzy_pairs}

        # Remove the originals — they'll be replaced by one paired delta
        # per (left, right) tuple below.
        deltas = [
            d for d in deltas
            if not (
                (d["classification"] == "removed_test" and d["test_fingerprint"] in paired_removed_fps)
                or (d["classification"] == "new_test" and d["test_fingerprint"] in paired_added_fps)
            )
        ]
        counts["removed_test"] -= len(paired_removed_fps)
        counts["new_test"] -= len(paired_added_fps)

        for left_tc, right_tc, score in fuzzy_pairs:
            base_class = _classify(
                left_tc.status, right_tc.status,
                left_tc.duration_ms, right_tc.duration_ms,
            )
            # Same-status-passed pairs return None from _classify. We
            # still surface them in the diff (tagged as ``renamed``) so
            # the user sees the rename.
            classification = base_class or "renamed"
            counts[classification] = counts.get(classification, 0) + 1

            delta_duration = (
                (right_tc.duration_ms - left_tc.duration_ms)
                if right_tc.duration_ms is not None and left_tc.duration_ms is not None
                else None
            )
            deltas.append({
                # Right-side fingerprint wins — the post-rename identity
                # is how the user now refers to the test.
                "test_fingerprint": right_tc.test_fingerprint,
                "test_name": right_tc.test_name,
                "suite_name": right_tc.suite_name,
                "left_status": str(left_tc.status) if left_tc.status else None,
                "right_status": str(right_tc.status) if right_tc.status else None,
                "left_duration_ms": left_tc.duration_ms,
                "right_duration_ms": right_tc.duration_ms,
                "delta_duration_ms": delta_duration,
                "classification": classification,
                "paired_by": "fuzzy_name_match",
                "previous_test_name": left_tc.test_name,
                "previous_test_fingerprint": left_tc.test_fingerprint,
            })

    # Sort: most urgent categories first, then by largest duration delta.
    priority = {
        "new_failure": 0,
        "regressed": 1,
        "still_failing": 2,
        "duration_spike": 3,
        "removed_test": 4,
        "fixed": 5,
        "improved": 6,
        "new_test": 7,
        "renamed": 8,
    }
    deltas.sort(
        key=lambda d: (
            priority.get(d["classification"], 99),
            -(d["delta_duration_ms"] if d["delta_duration_ms"] is not None else 0),
        )
    )

    truncated = len(deltas) > _MAX_DELTA_ROWS
    if truncated:
        deltas = deltas[:_MAX_DELTA_ROWS]

    left_summary = _summary_dict(
        left_run,
        scoped_tests=list(left_tests.values()) if suite_name else None,
        suite_name=suite_name,
    )
    right_summary = _summary_dict(
        right_run,
        scoped_tests=list(right_tests.values()) if suite_name else None,
        suite_name=suite_name,
    )

    delta_pass_rate: Optional[float] = None
    if left_summary["pass_rate"] is not None and right_summary["pass_rate"] is not None:
        delta_pass_rate = round(float(right_summary["pass_rate"]) - float(left_summary["pass_rate"]), 3)

    delta_duration_ms: Optional[int] = None
    if left_summary["duration_ms"] is not None and right_summary["duration_ms"] is not None:
        delta_duration_ms = int(right_summary["duration_ms"]) - int(left_summary["duration_ms"])

    return {
        "left": left_summary,
        "right": right_summary,
        "scope": "suite" if suite_name else "run",
        "suite_name": suite_name,
        "selection": selection,
        "ai_report": ai_report,
        "delta_total": right_summary["total_tests"] - left_summary["total_tests"],
        "delta_passed": right_summary["passed_tests"] - left_summary["passed_tests"],
        "delta_failed": right_summary["failed_tests"] - left_summary["failed_tests"],
        "delta_broken": right_summary["broken_tests"] - left_summary["broken_tests"],
        "delta_skipped": right_summary["skipped_tests"] - left_summary["skipped_tests"],
        "delta_pass_rate": delta_pass_rate,
        "delta_duration_ms": delta_duration_ms,
        "new_failures": counts["new_failure"],
        "fixed": counts["fixed"],
        "still_failing": counts["still_failing"],
        "regressed": counts["regressed"],
        "improved": counts["improved"],
        "new_tests": counts["new_test"],
        "removed_tests": counts["removed_test"],
        "duration_spikes": counts["duration_spike"],
        "renamed": counts["renamed"],
        "test_deltas": deltas,
        "truncated": truncated,
    }
