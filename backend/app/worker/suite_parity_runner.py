"""W25a HYG-04 — the suite-membership parity runner: it owns the run row's commits (contract §2.1 R-1 … R-3).

``services/suite_membership_parity`` only reads; this module is what the daily
beat task and "Compare now" run, in the Celery worker (never on a request path):

* **R-1 claim**, committed before any work: a ``running`` row older than
  ``RUN_STALE_MINUTES`` (a dead worker's) is failed ``interrupted``, then ONE
  ``running`` row is inserted; ``uq_smpr_one_running`` makes a second
  concurrent claim fail → ``{"skipped": "already_running"}`` (no row, no metric);
* **R-2 compare**, every project (archived included) in id order, ONE project's
  rows in memory at a time, each project's read snapshot ended by a rollback (the
  claimed row is already committed);
* **R-3 outcome**: ``clean`` (zero BLOCKING keys) or ``diffs``, the totals, the
  zero-filled counts, the capped per-project lines and sample; committed;
* **failure** (any exception in R-2 / R-3, incl. ``SoftTimeLimitExceeded``):
  rolled back, then recorded ``failed`` + ``error_code`` (the exception TYPE
  name) on a FRESH session, opened only after the work's session is closed — a
  failed day breaks the streak; a beat task never raises out.

ONE session at a time, never two: one connection at the deepest point, so the
production worker pool (size 1 + overflow 1) keeps one spare. The harness never
writes ``suite_memberships``, ``suite_membership_events`` or
``canonical_test_cases``; its only writes are its own run row. Nothing here
sends mail, calls an integration or triggers an AI pipeline. Logs carry ids,
counts, statuses and exception TYPE names only.
"""
from __future__ import annotations

import time
import uuid
from datetime import timedelta
from typing import Any, Callable, Optional

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.metrics import suite_parity_runs_total
from app.models.postgres import Project, SuiteMembershipParityRun
from app.services import suite_membership_parity as parity
from app.services import suite_parity_vocab as vocab

_slog = structlog.get_logger(__name__)


def _factory(session_factory: Optional[Callable[[], Any]]) -> Callable[[], Any]:
    if session_factory is not None:
        return session_factory
    from app.db.postgres import get_session_factory

    return get_session_factory()


def _window_runs() -> int:
    from app.core.config import settings

    return int(settings.CANONICAL_DELETION_WINDOW_RUNS)


async def _claim(db: Any, trigger: str, requested_by_id: Optional[uuid.UUID], window_runs: int) -> Optional[uuid.UUID]:
    """R-1: recover a dead worker's row, insert the ``running`` row, COMMIT; None when one is already running."""
    await db.execute(
        update(SuiteMembershipParityRun)
        .where(
            SuiteMembershipParityRun.status == "running",
            SuiteMembershipParityRun.started_at < func.now() - timedelta(minutes=vocab.RUN_STALE_MINUTES),
        )
        .values(status="failed", error_code=vocab.INTERRUPTED_ERROR_CODE, finished_at=func.now())
    )
    run_id = uuid.uuid4()
    db.add(SuiteMembershipParityRun(
        id=run_id, trigger=trigger, requested_by_id=requested_by_id, status="running", window_runs=window_runs,
    ))
    try:
        await db.flush()
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if "uq_smpr_one_running" in str(exc.orig):
            return None
        raise
    return run_id


async def _record_failure(session_factory: Callable[[], Any], run_id: uuid.UUID, error_code: str) -> None:
    """``failed`` + the exception TYPE name, on a FRESH session (the work's session was rolled back and closed)."""
    async with session_factory() as db:
        await db.execute(
            update(SuiteMembershipParityRun)
            .where(SuiteMembershipParityRun.id == run_id, SuiteMembershipParityRun.status == "running")
            .values(status="failed", error_code=error_code[:64], finished_at=func.now())
        )
        await db.commit()


async def _compare_all(db: Any, run_id: uuid.UUID, window_runs: int) -> dict[str, Any]:
    """R-2 + R-3 on the work session: every project in id order, then the outcome row, COMMIT."""
    ids = [row[0] for row in (await db.execute(select(Project.id).order_by(Project.id))).all()]
    await db.rollback()                         # end the read snapshot; the claim is already committed
    totals = parity.ParityTotals()
    for project_id in ids:
        totals.add(await parity.compare_project(db, project_id, window_runs=window_runs))
        await db.rollback()                     # one project per snapshot; its rows are dropped here
    summary = totals.as_dict()
    status = "clean" if summary["blocking_diffs"] == 0 else "diffs"
    await db.execute(
        update(SuiteMembershipParityRun)
        .where(SuiteMembershipParityRun.id == run_id)
        .values(
            status=status,
            finished_at=func.now(),
            projects_checked=summary["projects_checked"],
            keys_compared=summary["keys_compared"],
            blocking_diffs=summary["blocking_diffs"],
            expected_divergences=summary["expected_divergences"],
            counts=summary["counts"],
            per_project=summary["per_project"],
            sample=summary["sample"],
        )
    )
    await db.commit()
    summary["status"] = status
    return summary


async def run_parity(
    trigger: str,
    requested_by_id: Optional[uuid.UUID] = None,
    *,
    session_factory: Optional[Callable[[], Any]] = None,
) -> dict[str, Any]:
    """Claim → compare every project → record the outcome. Returns
    ``{"run_id", "status", "projects", "blocking", "expected"}`` or ``{"skipped": "already_running"}``.
    Never raises out of the comparison: a failure is a ``failed`` row (the streak breaks)."""
    if trigger not in vocab.RUN_TRIGGERS:
        raise ValueError(f"unknown parity trigger {trigger!r}")
    factory = _factory(session_factory)
    window_runs = _window_runs()
    started = time.monotonic()
    failure: Optional[str] = None
    summary: dict[str, Any] = {}
    async with factory() as db:
        try:
            run_id = await _claim(db, trigger, requested_by_id, window_runs)
        except Exception as exc:  # noqa: BLE001 - no row was claimed: a missing day, never a clean one
            suite_parity_runs_total.labels(status="failed").inc()
            _slog.warning("suite_membership_parity_claim_failed", trigger=trigger, error_type=type(exc).__name__)
            return {"status": "failed", "error_code": type(exc).__name__}
        if run_id is None:
            _slog.info("suite_membership_parity_skipped", reason="already_running", trigger=trigger)
            return {"skipped": "already_running"}
        try:
            summary = await _compare_all(db, run_id, window_runs)
        except Exception as exc:  # noqa: BLE001 - incl. SoftTimeLimitExceeded; only the TYPE name is kept
            failure = type(exc).__name__
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001 - the connection itself may be what failed
                pass

    if failure is not None:
        try:
            await _record_failure(factory, run_id, failure)
        except Exception as exc:  # noqa: BLE001 - the next claim recovers the row as "interrupted"
            _slog.warning("suite_membership_parity_failure_unrecorded", run_id=str(run_id),
                          error_type=type(exc).__name__)
        suite_parity_runs_total.labels(status="failed").inc()
        _slog.warning("suite_membership_parity_failed", run_id=str(run_id), error_type=failure)
        return {"run_id": str(run_id), "status": "failed", "error_code": failure}

    status = summary["status"]
    if status == "clean":
        suite_parity_runs_total.labels(status="clean").inc()
    else:
        suite_parity_runs_total.labels(status="diffs").inc()
    _slog.info(
        "suite_membership_parity_finished",
        run_id=str(run_id),
        trigger=trigger,
        status=status,
        projects=summary["projects_checked"],
        keys=summary["keys_compared"],
        blocking=summary["blocking_diffs"],
        expected=summary["expected_divergences"],
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return {
        "run_id": str(run_id),
        "status": status,
        "projects": summary["projects_checked"],
        "blocking": summary["blocking_diffs"],
        "expected": summary["expected_divergences"],
    }
