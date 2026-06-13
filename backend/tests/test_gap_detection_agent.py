"""
Behavioral coverage for the AIQ-P4 GapDetectionAgent.run().

The static ratchet (``test_architectural_agent_contracts.py``) guarantees the
agent *calls* ``validate_agent_contract``; these tests exercise the *runtime*
coverage-audit logic: how failed tests are partitioned into analyzed / skipped /
errored buckets, how analyzed tests are sub-classified by quality, the
referential-integrity invariant, and the never-raise fallback contract.

DB-free: the three BaseAgent side-effect methods (``mark_stage_running``,
``mark_stage_done``, ``broadcast_progress``) are stubbed with ``AsyncMock`` —
exactly the idiom the existing agent ``run()`` tests use — so no database
session or outbound call is touched. The analytic logic under test is never
mocked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agents.gap_detection_agent import GapDetectionAgent
from app.core.config import settings


def _agent() -> GapDetectionAgent:
    """A GapDetectionAgent with its DB/broadcast side-effects stubbed out."""
    agent = GapDetectionAgent()
    agent.mark_stage_running = AsyncMock()  # type: ignore[method-assign]
    agent.mark_stage_done = AsyncMock()  # type: ignore[method-assign]
    agent.broadcast_progress = AsyncMock()  # type: ignore[method-assign]
    return agent


def _state(**overrides):
    state = {
        "pipeline_run_id": "run-gap-1",
        "project_id": "proj-1",
        "failed_test_ids": [],
        "analyses": {},
    }
    state.update(overrides)
    return state


def _gap_report(result: dict) -> dict:
    return result["gap_report"]


def _contract(result: dict) -> dict:
    return result["agent_contracts"]["gap_detection"]


def _reasons_by_test(report: dict) -> dict[str, str]:
    return {g["test_id"]: g["reason"] for g in report["gaps"]}


def _buckets_by_test(report: dict) -> dict[str, str]:
    return {g["test_id"]: g["bucket"] for g in report["gaps"]}


# ── All-analyzed, clean ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_analyzed_clean_full_coverage_no_gaps():
    """N failed, all present with good analyses -> full coverage, no gaps."""
    n = 3
    failed = [f"t{i}" for i in range(n)]
    analyses = {
        tid: {
            "failure_category": "PRODUCT_BUG",
            "evidence_references": [{"source": "log", "excerpt": "x"}],
            "confidence_score": 95,
        }
        for tid in failed
    }

    result = await _agent().run(_state(failed_test_ids=failed, analyses=analyses))

    report = _gap_report(result)
    assert report["failed_count"] == n
    assert report["analyzed_count"] == n
    assert report["skipped_count"] == 0
    assert report["errored_count"] == 0
    assert report["coverage_ratio"] == 1.0
    assert report["integrity_ok"] is True
    # Clean analyzed tests emit no gap.
    assert report["gaps"] == []

    assert result["completed_stages"] == ["gap_detection"]
    assert result["current_stage"] == "report_refinement"

    contract = _contract(result)
    assert isinstance(contract["confidence_score"], int)
    assert 0 <= contract["confidence_score"] <= 100
    assert contract["evidence_count"] >= 1
    assert contract["decision_reason"]


# ── Mixed split: analyzed / skipped / errored ─────────────────────────────────


@pytest.mark.asyncio
async def test_mixed_split_partitions_and_integrity_holds():
    """One analyzed, one skipped, one errored, one timed_out -> exact counts and
    the referential-integrity invariant analyzed+skipped+errored == failed.
    """
    failed = ["a", "b", "c", "d"]
    analyses = {
        "a": {  # clean analyzed
            "failure_category": "PRODUCT_BUG",
            "evidence_references": [{"source": "log"}],
            "confidence_score": 90,
        },
        # "b" missing entirely -> skipped
        "c": {"error": "boom"},        # errored
        "d": {"timed_out": True},      # errored (timeout)
    }

    result = await _agent().run(_state(failed_test_ids=failed, analyses=analyses))
    report = _gap_report(result)

    assert report["failed_count"] == 4
    assert report["analyzed_count"] == 1
    assert report["skipped_count"] == 1
    assert report["errored_count"] == 2

    # REFERENTIAL INTEGRITY: every failed test lands in exactly one bucket.
    assert (
        report["analyzed_count"]
        + report["skipped_count"]
        + report["errored_count"]
        == report["failed_count"]
    )
    assert report["integrity_ok"] is True

    reasons = _reasons_by_test(report)
    buckets = _buckets_by_test(report)
    # Skipped id -> unanalyzed / skipped bucket.
    assert reasons["b"] == "unanalyzed"
    assert buckets["b"] == "skipped"
    # Errored (error) and timed-out entries -> errored / errored bucket.
    assert reasons["c"] == "errored"
    assert buckets["c"] == "errored"
    assert reasons["d"] == "errored"
    assert buckets["d"] == "errored"
    # The clean analyzed test emits no gap.
    assert "a" not in reasons


# ── Quality sub-classification of analyzed tests ──────────────────────────────


@pytest.mark.asyncio
async def test_quality_subclassification_inconclusive_no_evidence_low_confidence():
    """Analyzed tests sub-classify by quality: UNKNOWN -> inconclusive, empty
    evidence -> no_evidence, 0<conf<threshold -> low_confidence.
    """
    threshold = settings.AI_CONFIDENCE_THRESHOLD
    failed = ["u", "n", "lc"]
    analyses = {
        # UNKNOWN category -> inconclusive (highest priority).
        "u": {
            "failure_category": "UNKNOWN",
            "evidence_references": [{"source": "log"}],
            "confidence_score": 95,
        },
        # Has a category but empty evidence -> no_evidence.
        "n": {
            "failure_category": "PRODUCT_BUG",
            "evidence_references": [],
            "confidence_score": 95,
        },
        # Category + evidence, but confidence below threshold -> low_confidence.
        "lc": {
            "failure_category": "PRODUCT_BUG",
            "evidence_references": [{"source": "log"}],
            "confidence_score": threshold - 1,
        },
    }

    result = await _agent().run(_state(failed_test_ids=failed, analyses=analyses))
    report = _gap_report(result)

    # All three count as analyzed (the gap is a quality flag, not a coverage gap).
    assert report["analyzed_count"] == 3
    assert report["skipped_count"] == 0
    assert report["errored_count"] == 0
    assert report["inconclusive_count"] == 1
    assert report["no_evidence_count"] == 1

    reasons = _reasons_by_test(report)
    buckets = _buckets_by_test(report)
    assert reasons["u"] == "inconclusive"
    assert reasons["n"] == "no_evidence"
    assert reasons["lc"] == "low_confidence"
    # Quality gaps stay in the analyzed bucket.
    assert buckets["u"] == "analyzed"
    assert buckets["n"] == "analyzed"
    assert buckets["lc"] == "analyzed"


# ── Edge cases ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zero_failed_tests_is_clean_full_coverage():
    """failed_count==0 -> integrity ok, coverage 1.0, no gaps."""
    result = await _agent().run(_state(failed_test_ids=[], analyses={}))
    report = _gap_report(result)

    assert report["failed_count"] == 0
    assert report["coverage_ratio"] == 1.0
    assert report["integrity_ok"] is True
    assert report["gaps"] == []


@pytest.mark.asyncio
async def test_empty_analyses_with_failures_all_skipped_zero_coverage():
    """N failed but no analyses -> all skipped, coverage 0.0, integrity holds."""
    failed = ["x", "y"]
    result = await _agent().run(_state(failed_test_ids=failed, analyses={}))
    report = _gap_report(result)

    assert report["failed_count"] == 2
    assert report["analyzed_count"] == 0
    assert report["skipped_count"] == 2
    assert report["errored_count"] == 0
    assert report["coverage_ratio"] == 0.0
    assert report["integrity_ok"] is True
    assert {g["reason"] for g in report["gaps"]} == {"unanalyzed"}


# ── Never-raise contract on malformed / non-dict state ────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_state", [None, [], "not-a-dict", 42])
async def test_non_dict_state_returns_fallback_contract(bad_state):
    """A non-dict state degrades to a deterministic fallback contract, no raise."""
    result = await _agent().run(bad_state)

    assert "gap_report" in result
    contract = _contract(result)
    assert contract["fallback_used"] is True
    assert contract["confidence_score"] == 0
    assert result["completed_stages"] == ["gap_detection"]
    assert result["current_stage"] == "report_refinement"


@pytest.mark.asyncio
async def test_malformed_dict_state_never_raises_and_returns_contract():
    """A dict with malformed fields (analyses as a list, failed ids as an int)
    must not raise — it degrades the malformed parts to empty.
    """
    result = await _agent().run(
        {
            "pipeline_run_id": "run-gap-bad",
            "project_id": "proj-1",
            "failed_test_ids": 5,          # not a list -> coerced to []
            "analyses": ["not", "a", "dict"],  # not a dict -> coerced to {}
        }
    )
    report = _gap_report(result)
    # int failed_test_ids coerces to [] -> nothing to audit.
    assert report["failed_count"] == 0
    assert report["integrity_ok"] is True
    # A valid (non-fallback) contract is still stamped.
    assert "gap_detection" in result["agent_contracts"]


# ── Id coercion: int ids + str analyses keys partition correctly ──────────────


@pytest.mark.asyncio
async def test_int_failed_ids_with_str_analysis_keys_partition_correctly():
    """Integer failed ids and string analyses keys must align after coercion."""
    failed = [101, 102]
    analyses = {
        "101": {
            "failure_category": "PRODUCT_BUG",
            "evidence_references": [{"source": "log"}],
            "confidence_score": 90,
        },
        # 102 absent -> skipped.
    }

    result = await _agent().run(_state(failed_test_ids=failed, analyses=analyses))
    report = _gap_report(result)

    assert report["failed_count"] == 2
    assert report["analyzed_count"] == 1
    assert report["skipped_count"] == 1
    assert report["integrity_ok"] is True
    reasons = _reasons_by_test(report)
    assert reasons["102"] == "unanalyzed"
