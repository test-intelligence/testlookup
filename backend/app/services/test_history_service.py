"""One project's recent history of a test, in run order.

Every deterministic classifier reads a test's history to decide between "this
test is flaky" and "this test broke". The analysis pipeline used to count a
fingerprint's results across every project and all time, then hand the rules
engine two numbers: ``pass_count`` and ``fail_count``. That had two effects
seen on a running install (product findings D-02 and D-07):

* A fingerprint is ``hash(test_name + class_name)``, not salted per project, so
  a second project holding the same tests doubled the counts ("16/106 runs" on
  a project with 53 runs). One project's results shaped another project's
  classification and the sentence shown to its users.
* Counts carry no order. A test that passed 43 times and then failed 9 runs in
  a row with one error has a 17 % failure rate, which the flaky rule read as
  "passes and fails non-deterministically" and advised quarantine.

This loader is the single source for that history. It is scoped to one
project, bounded to a recent window, and ordered, so the intermittency label
(``flaky_signals``) and the streak lengths can be computed from it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import structlog
from sqlalchemy import func, select

from app.models.postgres import TestCase, TestRun
from app.services.flaky_signals import compute_intermittency_signals

logger = structlog.get_logger(__name__)

HISTORY_WINDOW_DAYS = 30
HISTORY_MAX_RESULTS = 60

_FAILED = {"FAILED", "BROKEN"}
_PASSED = {"PASSED"}


def _status_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "").upper()


def summarise_history(records: list[dict]) -> dict:
    """Turn an ordered list of results (oldest first) into the history dict.

    ``records`` hold ``status`` and, optionally, ``error_message``,
    ``stack_trace``, ``retry_count`` and ``is_flaky_run``. Skipped and other
    statuses count towards neither passes nor failures.
    """
    statuses = [_status_text(r.get("status")) for r in records]
    pass_count = sum(1 for s in statuses if s in _PASSED)
    fail_count = sum(1 for s in statuses if s in _FAILED)

    consecutive_failures = 0
    consecutive_passes = 0
    counted = [s for s in statuses if s in _PASSED or s in _FAILED]
    index = len(counted) - 1
    while index >= 0 and counted[index] in _FAILED:
        consecutive_failures += 1
        index -= 1
    while index >= 0 and counted[index] in _PASSED:
        consecutive_passes += 1
        index -= 1

    signals = compute_intermittency_signals(records)
    return {
        "pass_count": pass_count,
        "fail_count": fail_count,
        "consecutive_failures": consecutive_failures,
        "consecutive_passes": consecutive_passes,
        "intermittency_label": signals.intermittency_label,
        "status_volatility": signals.status_volatility,
        "error_signature_diversity": signals.error_signature_diversity,
        "window_days": HISTORY_WINDOW_DAYS,
    }


async def load_test_history(
    db,
    project_id,
    fingerprints: Iterable[str],
    *,
    window_days: int = HISTORY_WINDOW_DAYS,
    max_results: int = HISTORY_MAX_RESULTS,
    now: datetime | None = None,
) -> dict[str, dict]:
    """``{fingerprint: history}`` for one project's recent runs.

    Results are ordered by the run's start time (its upload time when the
    report carried none), oldest first, and limited to the newest
    ``max_results`` per fingerprint inside ``window_days``. A fingerprint with
    no rows in the window is absent from the answer.

    Raises on a database error; callers decide whether history is optional.
    """
    fps = sorted({fp for fp in fingerprints if fp})
    if not fps or project_id is None:
        return {}

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=window_days)
    run_time = func.coalesce(TestRun.start_time, TestRun.created_at)
    rank = func.row_number().over(
        partition_by=TestCase.test_fingerprint,
        order_by=(run_time.desc(), TestCase.created_at.desc()),
    ).label("rn")
    ranked = (
        select(
            TestCase.test_fingerprint.label("fp"),
            TestCase.status.label("status"),
            TestCase.error_message.label("error_message"),
            TestCase.retry_count.label("retry_count"),
            TestCase.is_flaky_run.label("is_flaky_run"),
            run_time.label("run_time"),
            TestCase.created_at.label("created_at"),
            rank,
        )
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(
            TestRun.project_id == project_id,
            TestCase.test_fingerprint.in_(fps),
            run_time >= cutoff,
        )
        .subquery()
    )
    stmt = (
        select(
            ranked.c.fp,
            ranked.c.status,
            ranked.c.error_message,
            ranked.c.retry_count,
            ranked.c.is_flaky_run,
        )
        .where(ranked.c.rn <= max_results)
        .order_by(ranked.c.fp, ranked.c.run_time.asc(), ranked.c.created_at.asc())
    )

    by_fp: dict[str, list[dict]] = {}
    for row in (await db.execute(stmt)).all():
        by_fp.setdefault(row.fp, []).append(
            {
                "status": row.status,
                "error_message": row.error_message,
                "retry_count": row.retry_count,
                "is_flaky_run": row.is_flaky_run,
            }
        )
    return {fp: summarise_history(records) for fp, records in by_fp.items()}
