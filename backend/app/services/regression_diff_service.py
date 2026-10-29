"""
Regression diff service — extracted from runs router (P3-9).

Computes a "What changed since last good run?" comparison between
the current test run and the most recent passing baseline.

The commit list (W10 FAIL-09) is the run's STORED per-project commit range --
the one the Suspects panel shows, resolved through the project's GitHub /
GitHub Enterprise integration by ``commit_attribution_service`` -- read here
read-only: no HTTP and no write, so a GET never writes on every view and the
deep stage that calls this inside its session makes no call either. The env
``GITHUB_TOKEN`` / ``GITHUB_REPO`` are not read (they bypassed the egress guard
and the offline gate). When no commits are listed, ``commit_range_status``
says why, in the vocabulary of ``COMMIT_RANGE_REASONS``.
"""
import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import RunBaseline, TestCase, TestRun
from app.models.schemas import CommitRangeStatus, RegressionDiffCommit
from app.services.run_diff_service import config_drift

logger = logging.getLogger("services.regression_diff")

#: Why the Changes tab lists no commits, in the order decided. The first six
#: are ``github_target.GITHUB_UNAVAILABLE_REASONS``.
COMMIT_RANGE_REASONS = (
    "offline_mode", "github_checks_off", "no_integration", "integration_disabled", "repo_mismatch",
    "no_token", "no_commit_hash", "no_baseline_commit", "not_resolved", "fetch_failed", "no_commits",
)

#: At most this many commits are listed (the count is the stored range's).
_LISTED_COMMITS = 50


def _uuid_or_none(value: Any) -> Optional[uuid.UUID]:
    if not value:
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        return None


async def never_reached_the_connector(db: AsyncSession, run: TestRun, serialized: dict) -> bool:
    """Whether a stored ``unavailable`` range was decided without ever asking GitHub.

    ``resolve_commit_range`` anchors the range (picks the base run) only when
    the run has a head commit and the ``github_checks`` gate is open. A row
    written while the gate was shut (the flag off, offline, a resolve that saw
    no head yet) therefore carries NO base commit even though a baseline run
    exists -- it does not mean "no earlier run carries a commit". Such a row is
    a gated outcome, not a fact about the run: it reads ``not_resolved`` and
    the lazy resolve re-resolves it as soon as the target is available, instead
    of showing a wrong reason for the 6 h cooldown.

    True when the row is ``unavailable`` with no base commit, the run has a
    commit hash, and a base anchor exists NOW (``resolve_base_anchor``: DB
    reads only). A row whose anchor still does not exist is a real
    ``no_baseline_commit``; a row WITH a base commit reached the connector (a
    fetch failure keeps its cooldown)."""
    from app.services.commit_attribution_service import resolve_base_anchor

    if serialized.get("available") or serialized.get("source") != "unavailable":
        return False
    if not serialized.get("resolved_at") or serialized.get("base_commit") or not run.commit_hash:
        return False
    base_run, _base_source = await resolve_base_anchor(db, run)
    return base_run is not None and bool(getattr(base_run, "commit_hash", None))


async def commit_range_status(db: AsyncSession, run: TestRun, serialized: dict) -> CommitRangeStatus:
    """Why the run's stored commit range lists commits, or why it does not.

    Read-only, and the PAT is never decrypted (``github_target_status`` reads
    ``has_pat``). ``reason``, in order: listed -> None; an empty range
    the CI supplied itself -> ``no_commits``; offline mode ->
    ``offline_mode``; the run has no commit hash -> ``no_commit_hash``; the
    project's GitHub target is unavailable -> its reason (``github_checks_off``,
    ``no_integration``, ``integration_disabled``, ``repo_mismatch``,
    ``no_token``); never resolved, or resolved only while the gate was shut
    (``never_reached_the_connector``) -> ``not_resolved``; no earlier run
    carries a commit -> ``no_baseline_commit``; base == head -> ``no_commits``;
    an ``unavailable`` row -> ``fetch_failed``; else ``no_commits``.
    """
    from app.services.github_target import github_target_status

    base_run_id = _uuid_or_none(serialized.get("base_run_id"))
    base_build_number: Optional[str] = None
    if base_run_id is not None:
        base_build_number = (
            await db.execute(select(TestRun.build_number).where(TestRun.id == base_run_id))
        ).scalar_one_or_none()

    available = bool(serialized.get("available"))
    reason: Optional[str] = None
    if not available:
        if serialized.get("source") == "supplied":
            # The CI supplied the range itself (no GitHub call): it was empty.
            reason = "no_commits"
        elif settings.AI_OFFLINE_MODE:
            reason = "offline_mode"
        elif not run.commit_hash:
            reason = "no_commit_hash"
        else:
            target_reason, _api_base = await github_target_status(db, run.project_id, ci_repo=run.ci_repo)
            if target_reason:
                reason = target_reason
            elif not serialized.get("resolved_at"):
                reason = "not_resolved"
            elif not serialized.get("base_commit"):
                stale = await never_reached_the_connector(db, run, serialized)
                reason = "not_resolved" if stale else "no_baseline_commit"
            elif serialized.get("base_commit") == serialized.get("head_commit"):
                reason = "no_commits"
            elif serialized.get("source") == "unavailable":
                reason = "fetch_failed"
            else:
                reason = "no_commits"

    return CommitRangeStatus(
        available=available,
        source=str(serialized.get("source") or "unavailable"),
        base_source=str(serialized.get("base_source") or "unavailable"),
        base_anchor_is_strong=bool(serialized.get("base_anchor_is_strong")),
        base_run_id=base_run_id,
        base_build_number=base_build_number,
        reason=reason,
        commit_count=len(serialized.get("commits") or []),
        resolved_at=serialized.get("resolved_at"),
    )


async def baseline_purged(db: AsyncSession, run_id: uuid.UUID) -> Optional[dict]:
    """The run's purged-baseline fact (W22a F-7), or None.

    The retention executor stamps ``run_baselines.baseline_purged_at`` (the
    FIRST purge date) before it deletes a run other runs used as their
    baseline; the SET NULL then clears ``baseline_run_id`` while the plain-text
    ``baseline_build_number`` survives. ONE read of THIS run's row (``run_id``
    is unique), read-only: ``{"build_number", "purged_at"}`` when stamped.
    """
    result = await db.execute(
        select(RunBaseline.baseline_build_number, RunBaseline.baseline_purged_at).where(
            RunBaseline.run_id == run_id,
            RunBaseline.baseline_purged_at.is_not(None),
        )
    )
    rows = list(result.all())
    if not rows:
        return None
    build_number, purged_at = rows[0][0], rows[0][1]
    if purged_at is None:
        return None
    return {
        "build_number": str(build_number) if build_number is not None else None,
        "purged_at": purged_at.isoformat(),
    }


def _listed(commits: list[dict]) -> list[dict]:
    return [
        RegressionDiffCommit(
            sha=str(c.get("sha") or "")[:8],
            message=c.get("message") or "",
            author=c.get("author"),
            timestamp=c.get("committed_at"),
            full_sha=c.get("sha"),
            commit_url=c.get("commit_url"),
        ).model_dump()
        for c in commits[:_LISTED_COMMITS]
    ]


async def compute_regression_diff(run: TestRun, db: AsyncSession) -> dict:
    """Compare a test run against its most recent passing baseline.

    Returns a dict with new failing tests, resolved count, pass rate delta,
    the run's stored commit range (``commit_range``, ``commit_count``) and
    ``commit_range_status`` saying why no commits are listed. Both answers
    (baseline found or not) carry ``baseline_purged`` — ``{"build_number",
    "purged_at"}`` when the retention purge removed this run's baseline, else
    None (W22a F-7). The baseline-found answer also carries ``config_drift``
    (``run_diff_service.config_drift`` against that baseline) and each new
    failure's ``test_fingerprint`` (W23b). Read-only.
    """
    from app.services import commit_attribution_service

    run_id = run.id

    # Read ONCE, before choosing a baseline: the comparison below may find an
    # older surviving baseline, but the run page still says the original one
    # was purged.
    purged = await baseline_purged(db, run_id)

    # Find last passing baseline run (pass_rate >= threshold * 0.85) before this run
    baseline_result = await db.execute(
        select(TestRun)
        .where(
            TestRun.project_id == run.project_id,
            TestRun.id != run_id,
            TestRun.pass_rate >= settings.RELEASE_PASS_RATE_THRESHOLD * 0.85,
            TestRun.created_at < run.created_at,
        )
        .order_by(TestRun.created_at.desc())
        .limit(1)
    )
    baseline = baseline_result.scalar_one_or_none()

    if not baseline:
        return {
            "run_id": str(run_id),
            "baseline_run_id": None,
            "baseline_available": False,
            "message": "No recent passing baseline run found for this project",
            "baseline_purged": purged,
        }

    # Get failing test fingerprints in current run
    current_failed = await db.execute(
        select(TestCase.test_fingerprint, TestCase.test_name, TestCase.suite_name)
        .where(TestCase.test_run_id == run_id, TestCase.status.in_(["FAILED", "BROKEN"]))
    )
    current_failed_fps = {
        r.test_fingerprint: {"test_name": r.test_name, "suite_name": r.suite_name}
        for r in current_failed.all()
        if r.test_fingerprint
    }

    # Get failing test fingerprints in baseline run
    baseline_failed = await db.execute(
        select(TestCase.test_fingerprint)
        .where(TestCase.test_run_id == baseline.id, TestCase.status.in_(["FAILED", "BROKEN"]))
    )
    baseline_failed_fps = {r.test_fingerprint for r in baseline_failed.all() if r.test_fingerprint}

    # New failures = in current but not in baseline. The fingerprint travels
    # with each (W23b FAIL-12: the tab's "Narrow" opens the bisect read for it).
    new_failing = [
        {"test_name": info["test_name"], "suite_name": info["suite_name"], "test_fingerprint": fp}
        for fp, info in current_failed_fps.items()
        if fp not in baseline_failed_fps
    ][:50]

    # Resolved failures = in baseline but not in current
    resolved_count = len(baseline_failed_fps - set(current_failed_fps.keys()))
    pass_rate_delta = round((run.pass_rate or 0) - (baseline.pass_rate or 0), 2)

    # The stored per-project commit range (what the Suspects panel shows) and
    # why it lists nothing when it does not. No HTTP, no write.
    serialized = await commit_attribution_service.get_commit_range(db, run)
    status = await commit_range_status(db, run, serialized)
    commits = list(serialized.get("commits") or [])

    return {
        "run_id": str(run_id),
        "baseline_run_id": str(baseline.id),
        "baseline_available": True,
        "build_number": run.build_number,
        "baseline_build_number": baseline.build_number,
        "pass_rate": run.pass_rate,
        "baseline_pass_rate": baseline.pass_rate,
        "pass_rate_delta": pass_rate_delta,
        "new_failing_tests": new_failing,
        "new_failing_count": len(new_failing),
        "resolved_count": resolved_count,
        "commit_range": _listed(commits),
        # The stored range's count, not the listed length (a top-N length is
        # not a count).
        "commit_count": len(commits),
        "commit_range_status": status.model_dump(mode="json"),
        "baseline_purged": purged,
        # W23b FAIL-13: the run context / environment / run-field drift against
        # THIS baseline (the one the tab prints); pure, no read.
        "config_drift": config_drift(run, baseline),
    }
