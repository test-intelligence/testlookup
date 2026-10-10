"""E2E pass 2026-10-10: two ways the run analysis told a wrong story.

1. The baseline ("previous") runs were picked with ``id != current`` and no
   time bound. The AI queue runs behind ingestion, so the first run of a
   project was compared with a 100% run ingested 9 s AFTER it: the summary
   read "Pass rate dropped to 73.9% ... from the previous 100%", and failures
   were classified new/persistent against the future. Same in the regression
   watchman's baseline.
2. A failed explanation step stores "AI analysis could not complete ...
   (invalid_json)" as the root cause but keeps the classifier's category and
   confidence (100%), so it passed the summary's 50% floor and was quoted as a
   cause: "6 tests failing due to invalid JSON parsing issues".
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.regression


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _capture_db():
    db = MagicMock()
    stmts: list = []

    async def _execute(stmt, *a, **k):
        stmts.append(stmt)
        return MagicMock(all=lambda: [])

    db.execute = AsyncMock(side_effect=_execute)
    return db, stmts


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", [None, "main"])
async def test_the_anomaly_baseline_only_reads_runs_before_this_one(branch):
    from app.agents.anomaly_agent import AnomalyDetectionAgent as AnomalyAgent

    db, stmts = _capture_db()
    await AnomalyAgent()._get_baseline_pass_rate(db, str(uuid.uuid4()), str(uuid.uuid4()), branch=branch)
    assert stmts, "no baseline query ran"
    for stmt in stmts:
        assert "test_runs.created_at < (SELECT test_runs.created_at" in _sql(stmt), _sql(stmt)


@pytest.mark.asyncio
async def test_the_watchman_baseline_only_reads_runs_before_this_one():
    from app.agents.regression_watchman import RegressionWatchman as RegressionWatchmanAgent

    db, stmts = _capture_db()
    await RegressionWatchmanAgent()._find_baseline_runs(db, str(uuid.uuid4()), str(uuid.uuid4()), "main")
    assert "test_runs.created_at < (SELECT test_runs.created_at" in _sql(stmts[0])


def test_a_failed_explanation_is_not_quoted_as_a_root_cause():
    from app.agents.summary_agent import SummaryAgent

    failed = ("AI analysis could not complete. Reason: Could not parse structured output "
              "from agent (invalid_json). Manual investigation required.")
    context = SummaryAgent()._build_context(
        run_data={"total_tests": 3, "passed_tests": 1, "failed_tests": 2, "skipped_tests": 0,
                  "pass_rate": 33.3, "build_number": "b1"},
        anomaly_summary="",
        anomalies=[],
        analyses={
            "tc-1": {"failure_category": "INFRASTRUCTURE", "confidence_score": 100,
                     "root_cause_summary": failed, "test_name": "t_db"},
            "tc-2": {"failure_category": "PRODUCT_BUG", "confidence_score": 80,
                     "root_cause_summary": "Checkout total ignores the discount", "test_name": "t_cart"},
        },
    )
    assert "invalid_json" not in context and "could not parse" not in context.lower()
    assert "t_db" in context and "root cause not determined" in context
    assert "Checkout total ignores the discount" in context


def test_usable_root_cause_drops_schema_failures_and_keeps_real_ones():
    from app.agents.summary_agent import _usable_root_cause

    assert _usable_root_cause({"root_cause_summary": "AI analysis could not complete. Reason: timeout."}) == ""
    assert _usable_root_cause({"root_cause_summary": "x" * 30, "schema_validated": False}) == ""
    assert _usable_root_cause({"root_cause_summary": " pool exhausted "}) == "pool exhausted"
