"""Commit attribution router (thin HTTP layer) — Epic 8 US-8.1/US-8.2.

Endpoints (both project/run-guarded via ``require_run_access``):

  GET /api/v1/runs/{run_id}/commit-range
      The resolved commit range for a run: base/head commits + the commits
      landed since the last green run, with an honest ``available`` flag and
      the acquisition ``source`` (connector | supplied | unavailable). Lazily
      resolves once if no row exists yet (e.g. runs finalized before Epic 8).

  GET /api/v1/runs/{run_id}/suspects?cluster_id=|fingerprint=
      Deterministic suspect ranking for a newly-failing cluster/test — ranked
      commits with the overlapping-files rationale and the monorepo caveat.

  GET /api/v1/runs/{run_id}/bisect?fingerprint=
      W23b FAIL-12: one failing test of the run narrowed to its own last pass
      on the run's branch series (or on main / master, labelled) -- the good
      and bad builds, the runs in between that did not run it, and, online,
      the commits between the two builds through the existing gated, SSRF-
      guarded compare (cached in Redis by the SHA pair). The QUERY fingerprint
      must be a test case OF THE PATH RUN (any other value: the same 404).
      Writes no Postgres row and never calls ``lazy_resolve_commit_range``
      (``services/bisect_service``).

All logic lives in ``services/commit_attribution_service``. The one write --
the lazy resolve -- lives HERE (``lazy_resolve_commit_range``) because the
service is stage-only and the services' commit allow-list is at its bound; the
run's Changes tab (``GET /runs/{id}/regression-diff``) calls the same helper.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.deps import require_run_access
from app.db.postgres import AsyncSessionLocal, get_db, release_read_connection
from app.models.postgres import RunCommitRange, TestRun
from app.models.schemas import BisectResponse
from app.services import bisect_service
from app.services import commit_attribution_service as svc
from app.services.bisect_vocab import BISECT_ERRORS

logger = structlog.get_logger("routers.commit_attribution")

router = APIRouter(prefix="/api/v1/runs", tags=["Commit Attribution"])


async def _load_run(db: Any, run_id: uuid.UUID) -> TestRun:
    result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


async def lazy_resolve_commit_range(db: Any, run: TestRun) -> None:
    """Resolve a run's commit range at most once per cooldown, on its own session.

    A GET never persists a GATED outcome. Offline, a run without a commit
    hash, or a project whose GitHub target is unavailable right now
    (``github_target_status``: the ``github_checks`` flag off, no or a disabled
    integration, a repository mismatch, no token -- read without decrypting
    anything) -> nothing is written and no call is made; the Changes tab then
    shows that reason, and the first view after the admin fixes it resolves.

    Otherwise, reads the stored range on the request's session and resolves
    only when it ``needs_resolution`` (no row yet, or an ``unavailable`` row
    older than the 6 h cooldown) or the row ``never_reached_the_connector``
    (written while the gate was shut: no base commit although a baseline run
    exists). It then hands the request session's read connection back
    (``release_read_connection``) and works on a dedicated write session: a
    gated row is deleted first (committed, so the service's own read session
    no longer sees it and its cooldown does not keep the stale answer), then
    ``resolve_commit_range`` runs and is committed -- so a GET never holds two
    connections and never writes on every view. A row that reached the
    connector (a fetch failure: base commit set) keeps the 6 h cooldown.
    Best-effort: a failure is logged and the read goes on."""
    from app.services.github_target import github_target_status
    from app.services.regression_diff_service import never_reached_the_connector

    if settings.AI_OFFLINE_MODE or not run.commit_hash:
        return
    serialized = await svc.get_commit_range(db, run)
    if serialized.get("available"):
        return
    reason, _api_base = await github_target_status(db, run.project_id, ci_repo=run.ci_repo)
    if reason:
        return
    stale = await never_reached_the_connector(db, run, serialized)
    if not stale and not svc.needs_resolution(serialized):
        return
    await release_read_connection(db)
    try:
        async with AsyncSessionLocal() as write_db:
            if stale:
                await write_db.execute(
                    delete(RunCommitRange).where(
                        RunCommitRange.run_id == run.id,
                        RunCommitRange.source == svc.SOURCE_UNAVAILABLE,
                        RunCommitRange.base_commit.is_(None),
                    )
                )
                await write_db.commit()
            await svc.resolve_commit_range(write_db, run.id)
            await write_db.commit()
    except Exception as exc:
        logger.warning(
            "commit_range_lazy_resolve_failed",
            run_id=str(run.id),
            error_type=type(exc).__name__,
            error=str(exc),
        )


@router.get("/{run_id}/commit-range")
async def get_run_commit_range(
    run_id: uuid.UUID,
    db: Any = Depends(get_db),
    _: Any = Depends(require_run_access()),
):
    """Return the commit range associated with a run (US-8.1).

    If no range exists yet — or the last attempt is older than the service's
    re-resolve cooldown — attempts a one-shot resolution (supplied wins →
    connector → unavailable) in a dedicated write session so this GET stays
    read-only, then returns the freshly-resolved range. The cooldown gate
    means repeated GETs on a range-less run do NOT re-run the connector.
    """
    run = await _load_run(db, run_id)
    await lazy_resolve_commit_range(db, run)
    return await svc.get_commit_range(db, run)


@router.get("/{run_id}/suspects")
async def get_run_suspects(
    run_id: uuid.UUID,
    cluster_id: Optional[str] = Query(
        default=None, description="Failure cluster id (e.g. cl_001) to attribute",
    ),
    fingerprint: Optional[str] = Query(
        default=None, description="Test fingerprint to attribute (single test)",
    ),
    db: Any = Depends(get_db),
    _: Any = Depends(require_run_access()),
):
    """Return ranked suspect commits for a newly-failing cluster/test (US-8.2).

    Honest ``available: False`` when there's no commit range for the run.
    Ranking is deterministic + inspectable (per-commit overlapping files).
    """
    run = await _load_run(db, run_id)
    # Resolve the range on demand so suspects work even before finalize ran
    # the connector path (mirrors the commit-range endpoint's lazy resolve,
    # including its re-resolve cooldown gate).
    await lazy_resolve_commit_range(db, run)
    return await svc.rank_suspects(
        db, run, cluster_id=cluster_id, fingerprint=fingerprint,
    )


@router.get("/{run_id}/bisect", response_model=BisectResponse)
async def get_run_bisect(
    run_id: uuid.UUID,
    fingerprint: str = Query(
        ..., min_length=1, max_length=64, description="Fingerprint of a test case of THIS run",
    ),
    db: Any = Depends(get_db),
    _: Any = Depends(require_run_access()),
):
    """Narrow one failing test of the run to its own last pass (W23b FAIL-12).

    The path run is guarded by ``require_run_access`` (VIEWER included: a
    read); the QUERY ``fingerprint`` is scoped here to a test case OF THAT RUN
    -- another run's, another project's or an unknown fingerprint is the same
    404. A git-host failure, a timeout or Redis being down is a 200 whose
    ``compare.state`` / ``compare.reason`` says so, never an error status. A
    GET: no Postgres write, no lazy resolve.
    """
    run = await _load_run(db, run_id)
    try:
        test_name, _outcome = await bisect_service.fingerprint_in_run(db, run, fingerprint)
    except bisect_service.FingerprintNotInRun:
        raise HTTPException(
            status_code=404,
            detail={"code": "fingerprint_not_in_run", "message": BISECT_ERRORS["fingerprint_not_in_run"]},
        ) from None
    return await bisect_service.get_bisect(
        db, run, fingerprint, now=datetime.now(timezone.utc), test_name=test_name,
    )
