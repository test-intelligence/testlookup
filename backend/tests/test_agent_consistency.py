"""
Behavioral coverage for the AIQ-P2 self-critique / verification layer.

These tests are DB-free: they exercise the pure-local consistency checks in
``app.agents.consistency`` directly, plus the AnalysisAgent ``_validate_confidence``
self-corrections, without any database session or outbound call.
"""
from __future__ import annotations

import pytest

from app.agents.consistency import (
    ConsistencyCheck,
    check_analysis_consistency,
    check_release_consistency,
    check_summary_consistency,
    log_consistency_failures,
)


# ── SummaryAgent checks ───────────────────────────────────────────────────────


def test_summary_fabricated_flaky_id_fails_referential_integrity():
    structured = {
        "layer3_evidence_pack": {"flaky_test_ids": ["ghost-id"], "citations": []},
        "layer2_incident_view": {"release_impact": "GO", "criticality": "LOW"},
        "layer4_action_plan": {"fix_recommendations": []},
    }
    report = check_summary_consistency(
        structured=structured,
        run_data={"failed_tests": 1, "pass_rate": 90},
        failed_test_ids=["real-id"],
        analyses={"real-id": {}},
    )
    failed_names = {chk.name for chk in report.failed}
    assert "referential_integrity_flaky_ids" in failed_names
    ref_check = next(c for c in report.checks if c.name == "referential_integrity_flaky_ids")
    assert ref_check.severity == "error"
    assert "ghost-id" in ref_check.offending_refs
    assert "consistency_check_failed:" in report.decision_suffix()


def test_summary_all_valid_ids_pass():
    structured = {
        "layer3_evidence_pack": {
            "flaky_test_ids": ["real-id"],
            "citations": [{"test_id": "real-id"}],
        },
        "layer2_incident_view": {"release_impact": "GO", "criticality": "LOW"},
        "layer4_action_plan": {"fix_recommendations": []},
    }
    report = check_summary_consistency(
        structured=structured,
        run_data={"failed_tests": 0, "pass_rate": 100},
        failed_test_ids=["real-id"],
        analyses={"real-id": {}},
    )
    assert report.all_passed
    assert report.decision_suffix() == "; consistency_ok"


def test_summary_failed_zero_but_release_not_go_fails_cross_layer():
    structured = {
        "layer3_evidence_pack": {"flaky_test_ids": [], "citations": []},
        "layer2_incident_view": {"release_impact": "NO_GO"},
        "layer4_action_plan": {"fix_recommendations": []},
    }
    report = check_summary_consistency(
        structured=structured,
        run_data={"failed_tests": 0, "pass_rate": 100},
        failed_test_ids=[],
        analyses={},
    )
    failed_names = {chk.name for chk in report.failed}
    assert "cross_layer_release_signal" in failed_names


def test_summary_empty_layers_do_not_raise_and_pass():
    report = check_summary_consistency(
        structured={},
        run_data={},
        failed_test_ids=[],
        analyses={},
    )
    assert report.all_passed
    assert report.decision_suffix() == "; consistency_ok"


# ── ReleaseRiskAgent checks ───────────────────────────────────────────────────


def test_release_low_risk_narrative_with_high_score_fails_error():
    decision = {
        "recommendation": "NO_GO",
        "risk_score": 70,
        "reasoning": "Composite score reflects low risk and a safe release.",
        "blocking_issues": [],
    }
    report = check_release_consistency(decision)
    band_check = next(c for c in report.checks if c.name == "narrative_vs_score_band")
    assert band_check.passed is False
    assert band_check.severity == "error"


def test_release_recommendation_desync_with_policy_is_warning_without_is_error():
    # recommendation does not match band (score 70 → NO_GO band, rec GO).
    with_policy = check_release_consistency({
        "recommendation": "GO",
        "risk_score": 70,
        "reasoning": "",
        "blocking_issues": [],
        "policy_id": "policy-123",
    })
    pol_check = next(c for c in with_policy.checks if c.name == "recommendation_vs_score")
    assert pol_check.passed is False
    assert pol_check.severity == "warning"
    assert "policy_override_present" in pol_check.details

    without_policy = check_release_consistency({
        "recommendation": "GO",
        "risk_score": 70,
        "reasoning": "",
        "blocking_issues": [],
    })
    nopol_check = next(c for c in without_policy.checks if c.name == "recommendation_vs_score")
    assert nopol_check.passed is False
    assert nopol_check.severity == "error"


def test_release_go_with_blocking_issues_fails():
    report = check_release_consistency({
        "recommendation": "GO",
        "risk_score": 10,
        "reasoning": "",
        "blocking_issues": ["something is blocked"],
    })
    blk_check = next(c for c in report.checks if c.name == "blocking_issues_vs_recommendation")
    assert blk_check.passed is False
    assert blk_check.severity == "warning"


def test_release_generic_fallback_decision_passes():
    # Mirrors the agent's exception fallback decision shape.
    decision = {
        "recommendation": "CONDITIONAL_GO",
        "risk_score": 50,
        "dimension_scores": {},
        "blocking_issues": [],
        "conditions_for_go": ["Manual review required — automated assessment failed"],
        "reasoning": "Release risk agent encountered an error.",
    }
    report = check_release_consistency(decision)
    assert report.all_passed


# ── AnalysisAgent checks ──────────────────────────────────────────────────────


def test_analysis_check_missing_id_fails_coverage():
    report = check_analysis_consistency(
        analyses={"present-id": {"confidence_score": 50}},
        failed_test_ids=["present-id", "missing-id"],
    )
    cov_check = next(c for c in report.checks if c.name == "coverage_completeness")
    assert cov_check.passed is False
    assert "missing-id" in cov_check.offending_refs


def test_analysis_validate_confidence_unknown_caps_at_40():
    pytest.importorskip("asyncpg")
    from app.agents.analysis_agent import AnalysisAgent

    agent = AnalysisAgent()
    analysis = agent._validate_confidence({
        "confidence_score": 90,
        "failure_category": "UNKNOWN",
        "evidence_references": [{"source": "log", "excerpt": "x"}],
        "root_cause_summary": "A sufficiently detailed root cause summary string.",
        "tools_used": ["fetch_stacktrace"],
    })
    assert analysis["confidence_score"] <= 40
    rules = {adj["rule"] for adj in analysis.get("_confidence_adjustments", [])}
    assert "category_unknown_high_confidence" in rules


def test_analysis_validate_confidence_flaky_contradicted_by_history():
    pytest.importorskip("asyncpg")
    from app.agents.analysis_agent import AnalysisAgent

    agent = AnalysisAgent()
    analysis = agent._validate_confidence({
        "confidence_score": 70,
        "failure_category": "PRODUCT_BUG",
        "evidence_references": [{"source": "log", "excerpt": "x"}],
        "root_cause_summary": "A sufficiently detailed root cause summary string.",
        "tools_used": ["fetch_stacktrace"],
        "is_flaky": True,
        "flakiness_data": {"pass_count": 0, "fail_count": 5},
    })
    assert analysis["is_flaky"] is False


# ── Shared module ─────────────────────────────────────────────────────────────


def test_log_consistency_failures_emits_one_event_per_failed_check():
    import structlog

    report = check_release_consistency({
        "recommendation": "GO",
        "risk_score": 70,
        "reasoning": "low risk and safe to release",
        "blocking_issues": ["blocked"],
    })
    assert not report.all_passed

    with structlog.testing.capture_logs() as logs:
        log_consistency_failures(report, pipeline_run_id="run-1")

    events = [e for e in logs if e["event"] == "consistency_check_failed"]
    assert len(events) == len(report.failed)
    for event in events:
        assert event["agent"] == "release_risk"
        assert "check" in event
        assert event["severity"] in {"warning", "error"}
        assert "details" in event


def test_consistency_check_clamps_invalid_severity():
    chk = ConsistencyCheck(name="x", passed=False, severity="catastrophic")
    assert chk.severity == "warning"
    ok = ConsistencyCheck(name="y", passed=False, severity="error")
    assert ok.severity == "error"


# ── Regression: AIQ-P2 review findings ────────────────────────────────────────


# B1 — checks must NEVER raise on non-list / non-iterable inputs.


def test_b1_release_truthy_non_list_blocking_issues_does_not_raise():
    # len(int) used to crash; _as_list coerces it to [].
    report = check_release_consistency(
        {"recommendation": "GO", "risk_score": 5, "blocking_issues": 5}
    )
    assert report.agent == "release_risk"
    blk = next(c for c in report.checks if c.name == "blocking_issues_vs_recommendation")
    # Coerced to [] ⇒ no blocking issues ⇒ GO is fine.
    assert blk.passed is True


def test_b1_summary_truthy_non_list_flaky_ids_does_not_raise():
    # set/iterate over int used to crash.
    report = check_summary_consistency(
        structured={"layer3_evidence_pack": {"flaky_test_ids": 5}},
        run_data={},
        failed_test_ids=["a"],
        analyses={"a": {}},
    )
    assert report.agent == "summary"
    flaky = next(c for c in report.checks if c.name == "referential_integrity_flaky_ids")
    assert flaky.passed is True


def test_b1_summary_truthy_non_list_failed_test_ids_does_not_raise():
    # iterate over int (failed_test_ids=5) used to crash.
    report = check_summary_consistency(
        structured={},
        run_data={},
        failed_test_ids=5,
        analyses={},
    )
    assert report.agent == "summary"
    assert isinstance(report.checks, list)


def test_b1_analysis_truthy_non_list_failed_ids_does_not_raise():
    # iterate over int used to crash.
    report = check_analysis_consistency(analyses={"a": {}}, failed_test_ids=5)
    assert report.agent == "root_cause_analysis"
    cov = next(c for c in report.checks if c.name == "coverage_completeness")
    assert cov.passed is True


# M1 — int-keyed analyses must not cause false positives.


def test_m1_int_keyed_analysis_coverage_passes():
    # str(101) not in {101} used to falsely flag coverage_completeness.
    report = check_analysis_consistency(
        analyses={101: {"is_flaky": False, "confidence_score": 80}},
        failed_test_ids=[101],
    )
    cov = next(c for c in report.checks if c.name == "coverage_completeness")
    assert cov.passed is True
    assert cov.offending_refs == []


def test_m1_int_keyed_summary_universe_matches_stringified_ids():
    # Universe built from int analyses keys must match stringified flaky/citation ids.
    report = check_summary_consistency(
        structured={
            "layer3_evidence_pack": {
                "flaky_test_ids": ["101"],
                "citations": [{"test_id": 101}],
            },
        },
        run_data={},
        failed_test_ids=[101],
        analyses={101: {}},
    )
    flaky = next(c for c in report.checks if c.name == "referential_integrity_flaky_ids")
    cites = next(c for c in report.checks if c.name == "referential_integrity_citations")
    assert flaky.passed is True
    assert cites.passed is True


# M2 — negation-blind narrative match must not flag correct high-risk prose.


def test_m2_negated_low_risk_at_high_score_passes():
    report = check_release_consistency(
        {
            "recommendation": "NO_GO",
            "risk_score": 80,
            "reasoning": "Risk is not low risk; release is blocked.",
        }
    )
    band = next(c for c in report.checks if c.name == "narrative_vs_score_band")
    assert band.passed is True


def test_m2_unnegated_low_risk_at_high_score_still_fails():
    # Guard: the negation fix must not silence genuine contradictions.
    report = check_release_consistency(
        {
            "recommendation": "NO_GO",
            "risk_score": 80,
            "reasoning": "This is low risk and safe to release.",
        }
    )
    band = next(c for c in report.checks if c.name == "narrative_vs_score_band")
    assert band.passed is False
    assert band.severity == "error"


# M3 — conservative NO_GO at a low score band → warning, not error.


def test_m3_no_go_at_low_score_is_conservative_warning():
    report = check_release_consistency(
        {"recommendation": "NO_GO", "risk_score": 10, "reasoning": "pass rate floor"}
    )
    rec = next(c for c in report.checks if c.name == "recommendation_vs_score")
    assert rec.passed is False
    assert rec.severity == "warning"
    assert "conservative_override" in rec.details


def test_m3_less_conservative_go_at_high_score_stays_error():
    # The reverse direction (GO at a NO_GO band) is NOT safe ⇒ stays error.
    report = check_release_consistency(
        {"recommendation": "GO", "risk_score": 80, "reasoning": ""}
    )
    rec = next(c for c in report.checks if c.name == "recommendation_vs_score")
    assert rec.passed is False
    assert rec.severity == "error"


# Mn1 — empty universe with cited ids is a violation.


def test_mn1_empty_universe_with_cited_ids_flags_error():
    report = check_summary_consistency(
        structured={
            "layer3_evidence_pack": {
                "flaky_test_ids": ["ghost"],
                "citations": [{"test_id": "phantom"}],
            },
        },
        run_data={},
        failed_test_ids=[],
        analyses={},
    )
    flaky = next(c for c in report.checks if c.name == "referential_integrity_flaky_ids")
    cites = next(c for c in report.checks if c.name == "referential_integrity_citations")
    assert flaky.passed is False
    assert flaky.severity == "error"
    assert "ghost" in flaky.offending_refs
    assert cites.passed is False
    assert cites.severity == "error"
    assert "phantom" in cites.offending_refs


# MINOR — rescoped _validate_confidence rules genuinely record their adjustment.


def test_flaky_contradicts_history_records_adjustment():
    pytest.importorskip("asyncpg")
    from app.agents.analysis_agent import AnalysisAgent

    agent = AnalysisAgent()
    analysis = agent._validate_confidence({
        "confidence_score": 70,
        "failure_category": "PRODUCT_BUG",
        "evidence_references": [{"source": "log", "excerpt": "x"}],
        "root_cause_summary": "A sufficiently detailed root cause summary string.",
        "tools_used": ["fetch_stacktrace"],
        "is_flaky": True,
        "flakiness_data": {"pass_count": 0, "fail_count": 5},
    })
    assert analysis["is_flaky"] is False
    rules = {adj["rule"] for adj in analysis.get("_confidence_adjustments", [])}
    assert "flaky_contradicts_history" in rules


def test_evidence_count_vs_confidence_records_adjustment():
    pytest.importorskip("asyncpg")
    from app.agents.analysis_agent import AnalysisAgent

    agent = AnalysisAgent()
    # Raw confidence 75 in (60,80], no evidence, no tools. The no_evidence_references
    # cap lowers the live number to 50 first, so the rescoped rule must reason about
    # the RAW value to fire at all.
    analysis = agent._validate_confidence({
        "confidence_score": 75,
        "failure_category": "PRODUCT_BUG",
        "evidence_references": [],
        "root_cause_summary": "A sufficiently detailed root cause summary string.",
        "tools_used": [],
    })
    rules = {adj["rule"] for adj in analysis.get("_confidence_adjustments", [])}
    assert "evidence_count_vs_confidence" in rules
    # Never raises confidence back up — stays capped by the earlier rule.
    assert analysis["confidence_score"] <= 60
