"""Per-test failure detail: the suite, the names, the reasons.

Built for FR-001 and FR-002, which are one problem seen from two surfaces. The
owner's report was that reports and the chat summary say *how many* tests
failed but never *which*, never the suite, and never why — "text ... which lack
credible details".

That is not the model being vague. Nothing was fetching the detail:

* ``ConversationAgent._fetch_run_context`` selected run-level aggregates only —
  build, branch, status, counts, pass rate. No per-test rows, no suite.
* ``SummaryAgent._build_context`` built its bullets as
  ``[CATEGORY] conf=N%: <summary>``, binding the test id to ``_tc_id`` and
  throwing it away.

A model cannot name a test it was never shown, and telling it to "be specific"
without supplying the rows would invite it to invent names. So this module
supplies them.

Design notes that matter:

* **Suite comes from both places.** Old live-stream runs carry the suite only on
  the run (``primary_suite_name``), newer ingests put it on the case
  (``suite_name``). Reading one gives blank suites for half the corpus, which is
  the ``_effective_suite_sql`` rule this repo already learned the hard way.
* **The cap is disclosed, not silent.** A 400-failure run cannot go in a prompt.
  Truncating quietly turns a partial view into something a reader takes as
  complete, so the payload carries ``returned`` / ``total`` / ``truncated``.
* **Confidence travels with the category.** ``INFRASTRUCTURE`` at 95 and at 30
  are different claims. The analysis may also be absent entirely — that is
  reported as absent rather than defaulted to a category.
"""
from __future__ import annotations

from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AIAnalysis, TestCase, TestRun

logger = structlog.get_logger("services.failure_detail")

# How many failing tests to describe in full. Chosen to stay inside a prompt
# budget while covering the runs people actually read; anything beyond it is
# counted and disclosed, never dropped in silence.
DEFAULT_LIMIT = 25

# Failure text is quoted into prompts and reports. Long stack traces crowd out
# everything else without adding signal past the first few lines.
MAX_ERROR_CHARS = 400


def effective_suite(case_suite: Optional[str], run_suite: Optional[str]) -> Optional[str]:
    """The suite a reader would call this test's, from either source.

    Mirrors ``analytics_service._effective_suite_sql`` in Python: the case's own
    suite wins, the run's primary suite is the fallback. Reading only one leaves
    the suite blank for a whole class of runs.
    """
    for candidate in (case_suite, run_suite):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return None


async def failure_detail(
    db: AsyncSession,
    run_id: Any,
    *,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Named failing tests for a run, with suite, reason and classification.

    Returns a dict carrying ``suite`` (the run's primary suite, when it has
    one), ``failures`` (a list, most-confident classification first), and the
    disclosure fields ``returned`` / ``total`` / ``truncated``.

    Never raises: a reporting surface losing its detail is worse than losing the
    detail *and* the report, so callers get an empty-but-honest payload.
    """
    empty: dict[str, Any] = {
        "suite": None, "failures": [], "returned": 0, "total": 0,
        "truncated": False,
    }
    try:
        run = (
            await db.execute(select(TestRun).where(TestRun.id == run_id))
        ).scalar_one_or_none()
        if run is None:
            return empty

        rows = (
            await db.execute(
                select(TestCase, AIAnalysis)
                .outerjoin(AIAnalysis, AIAnalysis.test_case_id == TestCase.id)
                .where(
                    TestCase.test_run_id == run_id,
                    TestCase.status.in_(["FAILED", "BROKEN"]),
                )
            )
        ).all()

        failures: list[dict[str, Any]] = []
        for case, analysis in rows:
            error = (case.error_message or "").strip()
            failures.append({
                "test_name": case.test_name,
                "suite": effective_suite(case.suite_name, run.primary_suite_name),
                "status": str(case.status),
                "error_message": error[:MAX_ERROR_CHARS] or None,
                # Absent analysis is reported as absent. Defaulting to UNKNOWN
                # would be indistinguishable from a classifier that ran and
                # could not decide.
                "failure_category": (
                    str(analysis.failure_category) if analysis and analysis.failure_category
                    else None
                ),
                "confidence_score": analysis.confidence_score if analysis else None,
                "root_cause_summary": (
                    (analysis.root_cause_summary or "").strip()[:MAX_ERROR_CHARS] or None
                    if analysis else None
                ),
                "is_flaky": bool(analysis.is_flaky) if analysis else None,
            })

        # Most-confident classifications first so a truncated view keeps the
        # findings a reader can most rely on. Unclassified failures sort last
        # rather than being hidden — they are still real failures.
        failures.sort(
            key=lambda f: (f["confidence_score"] is None, -(f["confidence_score"] or 0),
                           f["test_name"] or ""),
        )
        total = len(failures)
        capped = failures[: max(1, limit)]
        return {
            "suite": run.primary_suite_name,
            "failures": capped,
            "returned": len(capped),
            "total": total,
            "truncated": total > len(capped),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("failure_detail_unavailable", run_id=str(run_id), error=str(exc)[:200])
        return empty


def format_for_prompt(detail: dict[str, Any]) -> str:
    """Render failure detail for a model or a report.

    Says plainly when there is nothing to show, and when the list was cut short.
    An unlabelled partial list is the thing this whole feature exists to stop.
    """
    failures = detail.get("failures") or []
    if not failures:
        return "Failing tests: none recorded for this run."

    lines: list[str] = []
    suite = detail.get("suite")
    if suite:
        lines.append(f"Suite: {suite}")

    header = f"Failing tests ({detail.get('returned')} of {detail.get('total')})"
    if detail.get("truncated"):
        header += (
            " — this list is TRUNCATED; do not describe it as the complete set"
        )
    lines.append(header + ":")

    for f in failures:
        bits = [f"- {f.get('test_name') or '(unnamed test)'}"]
        if f.get("suite"):
            bits.append(f"[suite: {f['suite']}]")
        if f.get("status"):
            bits.append(f"({f['status']})")
        lines.append(" ".join(bits))
        if f.get("error_message"):
            lines.append(f"    error: {f['error_message']}")
        if f.get("failure_category"):
            conf = f.get("confidence_score")
            conf_txt = f" at {conf}% confidence" if conf is not None else " (confidence unrecorded)"
            flaky = ", flagged flaky" if f.get("is_flaky") else ""
            lines.append(f"    classified: {f['failure_category']}{conf_txt}{flaky}")
        else:
            lines.append("    classified: not analysed")
        if f.get("root_cause_summary"):
            lines.append(f"    cause: {f['root_cause_summary']}")
    return "\n".join(lines)
