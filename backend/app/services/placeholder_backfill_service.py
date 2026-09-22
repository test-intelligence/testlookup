"""Retroactive placeholder synthesis for historical TestRuns.

When a live-stream run finalises with ``failed_tests > 0`` but the per-
event buffer was empty (SDK didn't emit ``test_result`` events, or the
Redis buffer was evicted before ``persist_live_session`` drained it),
no ``TestCase`` rows exist for the run. The aggregate counters
on ``TestRun`` reflect the SDK-reported totals but the per-test detail
is gone — and the action queue (``/my-failures``) has nothing to
attach an assignment to.

``persist_live_session`` now synthesizes placeholder rows at write
time so new runs don't fall into this hole (see ``worker.tasks``).
This module handles the **retroactive** case: walk every historical
TestRun whose ``failed_tests + broken_tests > 0`` and whose
``test_cases`` table has no rows, and synthesize the same kind of
placeholder rows so the user's existing data flows into the inbox.

Idempotent — only fires when ``COUNT(test_cases) = 0`` for the run.
A second pass produces no rows because the first pass populated
test_cases.
"""
from __future__ import annotations

import hashlib
import uuid as _uuid_mod
from typing import Any

import structlog
from sqlalchemy import func, insert as sa_insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import TestCase, TestRun, TestStatus

logger = structlog.get_logger(__name__)


def _placeholder_rows_for_run(
    run: TestRun, failed: int, broken: int,
) -> list[dict]:
    """Build the placeholder row payload mirroring
    ``worker.tasks.persist_live_session``'s synthesis exactly.

    The fingerprint includes the run id so re-running the task is a
    no-op (UNIQUE-style natural key) and so different runs of the same
    suite don't dedupe into a single canonical_test_case.
    """
    rows: list[dict] = []
    suite_default = (run.primary_suite_name or "").strip() or None
    total = int(failed) + int(broken)
    for i in range(total):
        status_value = (
            TestStatus.FAILED.value if i < int(failed)
            else TestStatus.BROKEN.value
        )
        fp = hashlib.md5(
            f"placeholder:{run.id}:{i}".encode()
        ).hexdigest()
        rows.append({
            "id": _uuid_mod.uuid4(),
            "test_run_id": run.id,
            "test_fingerprint": fp,
            "test_name": f"[ingestion gap — per-test detail unavailable] #{i + 1}",
            "suite_name": suite_default,
            "class_name": None,
            "status": status_value,
            "duration_ms": None,
            "error_message": (
                "Per-test events were lost during ingestion. "
                f"Run reported {int(failed)} failure(s) and "
                f"{int(broken)} broken test(s); re-run the "
                "suite to capture per-test detail."
            ),
            "tags": None,
        })
    return rows


async def backfill_placeholders_for_project(
    db: AsyncSession,
    project_id: _uuid_mod.UUID,
    max_runs: int = 500,
) -> dict:
    """Walk runs in one project, synthesize placeholders where the
    ``failed_tests + broken_tests > 0`` aggregate disagrees with the
    ``test_cases`` table.

    Caller owns the transaction. The function flushes per-run so a
    failure halfway through doesn't lose work already done — and the
    caller's ``commit`` materialises everything.
    """
    counts = {"runs_scanned": 0, "runs_filled": 0, "rows_synthesised": 0}

    # Candidate runs: any TestRun with a reported failure but no
    # test_cases rows. Bounded by ``max_runs`` so a project with
    # thousands of legacy runs doesn't run the worker timeout out.
    candidates_stmt = (
        select(TestRun)
        .where(
            TestRun.project_id == project_id,
            (
                func.coalesce(TestRun.failed_tests, 0)
                + func.coalesce(TestRun.broken_tests, 0)
            ) > 0,
        )
        .where(
            ~select(TestCase.id)
            .where(TestCase.test_run_id == TestRun.id)
            .exists()
        )
        .order_by(TestRun.created_at.desc())
        .limit(max_runs)
    )
    candidates = list((await db.execute(candidates_stmt)).scalars().all())

    for run in candidates:
        counts["runs_scanned"] += 1
        failed = int(run.failed_tests or 0)
        broken = int(run.broken_tests or 0)
        if failed + broken <= 0:
            continue
        rows = _placeholder_rows_for_run(run, failed, broken)
        if not rows:
            continue
        await db.execute(sa_insert(TestCase), rows)
        await db.flush()
        counts["runs_filled"] += 1
        counts["rows_synthesised"] += len(rows)

    logger.info(
        "placeholder_backfill_complete",
        project_id=str(project_id),
        **counts,
    )
    return counts


async def backfill_placeholders_all_projects(
    db: AsyncSession,
    max_runs_per_project: int = 500,
) -> dict:
    """Sweep every project. Used by the Celery beat / one-shot invocation."""
    from app.models.postgres import Project

    project_ids = [
        row[0] for row in (await db.execute(select(Project.id))).all()
    ]
    totals: dict[str, Any] = {
        "projects_scanned": 0,
        "runs_scanned": 0,
        "runs_filled": 0,
        "rows_synthesised": 0,
        "errors": 0,
        # Projects that received rows, so the caller can bump each one's
        # analytics epoch after its commit (VIZ-212).
        "project_ids": [],
    }
    # NOTE: caller's session is shared across projects only for the
    # candidate-discovery; we don't commit here. The Celery wrapper
    # opens a fresh session per project so a failure on one project
    # doesn't poison another.
    for project_id in project_ids:
        totals["projects_scanned"] += 1
        try:
            result = await backfill_placeholders_for_project(
                db, project_id, max_runs=max_runs_per_project,
            )
            totals["runs_scanned"] += int(result.get("runs_scanned", 0))
            totals["runs_filled"] += int(result.get("runs_filled", 0))
            totals["rows_synthesised"] += int(result.get("rows_synthesised", 0))
            if int(result.get("runs_filled", 0)):
                totals["project_ids"].append(str(project_id))
        except Exception as exc:
            totals["errors"] += 1
            logger.warning(
                "placeholder_backfill_project_failed",
                project_id=str(project_id), error=str(exc),
            )
    return totals
