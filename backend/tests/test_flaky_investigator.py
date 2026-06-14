"""FLK-P4 regression — flaky-investigation reasoning (pure, no-DB).

Covers failure clustering, likely-cause attribution (every branch incl. the
ML-skeptic override), and the structured evidence-backed verdict — plus the
never-raise invariant on malformed input.
"""
from __future__ import annotations

from app.services.flaky_investigator import (
    build_flaky_verdict,
    cluster_failures,
    determine_likely_cause,
)
from app.services.flaky_signals import IntermittencySignals, compute_intermittency_signals
from app.services.flaky_statistics import wilson_failure_confidence


def _sig(**kw) -> IntermittencySignals:
    base = dict(
        runs=10, fail_count=4, flip_count=5, status_volatility=0.5,
        error_signature_diversity=0.3, stack_trace_diversity=0.2,
        in_run_retry_rate=0.0, intermittency_label="intermittent_flaky",
    )
    base.update(kw)
    return IntermittencySignals(**base)


class TestClusterFailures:
    def test_counts_distinct_signatures_and_stacks(self):
        recs = [
            {"status": "FAILED", "error_message": "Timeout after 30s", "stack_trace": "at A\nat B"},
            {"status": "FAILED", "error_message": "Timeout after 45s", "stack_trace": "at A\nat B"},
            {"status": "FAILED", "error_message": "Connection refused", "stack_trace": "at C\nat D"},
            {"status": "PASSED"},
        ]
        c = cluster_failures(recs)
        assert c.total_failures == 3
        # "Timeout after 30s"/"45s" denoise to the same signature → 2 distinct.
        assert c.distinct_error_signatures == 2
        assert c.distinct_stack_fingerprints == 2
        assert c.dominant_error_count == 2  # the timeout signature

    def test_never_raises_on_garbage(self):
        for bad in (None, [None, 3], [{"status": object()}], [{"error_message": object()}]):
            c = cluster_failures(bad)
            assert c.total_failures >= 0

    def test_to_dict_shape(self):
        d = cluster_failures([{"status": "FAILED", "error_message": "x"}]).to_dict()
        assert set(d) == {
            "total_failures", "distinct_error_signatures", "distinct_stack_fingerprints",
            "dominant_error", "dominant_error_count", "examples",
        }


class TestDetermineLikelyCause:
    def test_insufficient_data(self):
        _, code = determine_likely_cause(_sig(intermittency_label="insufficient_data"))
        assert code == "insufficient_data"

    def test_ml_skeptic_overrides(self):
        _, code = determine_likely_cause(_sig(), ml_confidence=0.1)
        assert code == "likely_regression"

    def test_in_run_retry(self):
        _, code = determine_likely_cause(_sig(in_run_retry_rate=0.3))
        assert code == "in_run_retry"

    def test_environmental(self):
        _, code = determine_likely_cause(_sig(intermittency_label="environmental_flaky"))
        assert code == "environmental"

    def test_race_condition_from_stack_diversity(self):
        _, code = determine_likely_cause(_sig(stack_trace_diversity=0.8))
        assert code == "race_condition"

    def test_persistent_regression(self):
        _, code = determine_likely_cause(_sig(intermittency_label="persistent_regression"))
        assert code == "likely_regression"

    def test_intermittent(self):
        _, code = determine_likely_cause(_sig(intermittency_label="intermittent_flaky"))
        assert code == "intermittent"

    def test_low_volatility(self):
        _, code = determine_likely_cause(_sig(intermittency_label="low_volatility_flaky", stack_trace_diversity=0.0))
        assert code == "low_volatility"

    def test_high_ml_does_not_override_to_regression(self):
        _, code = determine_likely_cause(_sig(intermittency_label="intermittent_flaky"), ml_confidence=0.9)
        assert code == "intermittent"

    def test_always_returns_pair_of_strings(self):
        text, code = determine_likely_cause(_sig())
        assert isinstance(text, str) and isinstance(code, str) and text and code


class TestBuildFlakyVerdict:
    def test_shape_and_evidence(self):
        v = build_flaky_verdict(
            0.4, _sig(in_run_retry_rate=0.2),
            ml_confidence=0.85,
            wilson=wilson_failure_confidence(8, 20),
            clusters=cluster_failures([{"status": "FAILED", "error_message": "Timeout"}]),
            build_change_summary="3 commits touched the auth module",
        )
        assert set(v) == {
            "is_flaky", "confidence", "likely_cause", "likely_cause_code",
            "evidence", "confidence_breakdown",
        }
        assert isinstance(v["evidence"], list) and len(v["evidence"]) >= 3
        assert 0 <= v["confidence"] <= 100
        assert v["is_flaky"] is True  # in_run_retry → flaky

    def test_regression_verdict_is_not_flaky(self):
        v = build_flaky_verdict(
            0.4, _sig(intermittency_label="persistent_regression", stack_trace_diversity=0.0),
            ml_confidence=0.1,
        )
        assert v["is_flaky"] is False
        assert v["likely_cause_code"] == "likely_regression"

    def test_confidence_is_capped_without_strong_evidence(self):
        # Only weak signals (no ml, no retry, low diversities) → confidence
        # cannot exceed the AIQ-P3 cap of 70.
        v = build_flaky_verdict(
            0.3, _sig(status_volatility=0.1, error_signature_diversity=0.0,
                      stack_trace_diversity=0.0, in_run_retry_rate=0.0,
                      intermittency_label="low_volatility_flaky"),
        )
        assert v["confidence"] <= 70

    def test_never_raises_on_minimal_input(self):
        v = build_flaky_verdict(0.0, _sig(intermittency_label="insufficient_data"))
        assert v["is_flaky"] is False
        assert isinstance(v["evidence"], list)


class TestParityWithComputeSignals:
    def test_verdict_from_real_signals(self):
        recs = [
            {"status": "FAILED", "error_message": "Timeout", "stack_trace": "a", "retry_count": 1},
            {"status": "PASSED"},
            {"status": "FAILED", "error_message": "Conn reset", "stack_trace": "b"},
            {"status": "PASSED"},
            {"status": "FAILED", "error_message": "Timeout", "stack_trace": "a"},
        ]
        signals = compute_intermittency_signals(recs)
        v = build_flaky_verdict(0.6, signals, clusters=cluster_failures(recs))
        assert v["likely_cause"]
        assert 0 <= v["confidence"] <= 100
