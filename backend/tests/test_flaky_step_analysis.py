"""FLK-P5 regression — granular step-level failure attribution (pure, no-DB).

Covers the surgical attribution builder: assertion summary (expected/actual +
message fallback), the surgical-fix recommendation, stable step fingerprinting,
the no-failing-step path, and the never-raise invariant.
"""
from __future__ import annotations

from app.services.flaky_step_analysis import (
    StepFailureAttribution,
    build_step_attribution,
)


def _step(**kw) -> dict:
    base = dict(
        ordinal=3, name="click checkout", keyword="When",
        assertion_message=None, expected_value=None, actual_value=None,
        assertion_trace=None,
    )
    base.update(kw)
    return base


class TestBuildStepAttribution:
    def test_no_failing_step_returns_empty(self):
        a = build_step_attribution(None, total_steps=5, failing_step_count=0)
        assert a.has_failing_step is False
        assert a.total_steps == 5
        assert a.surgical_recommendation == ""

    def test_expected_actual_summary(self):
        a = build_step_attribution(
            _step(expected_value="200", actual_value="500"),
            total_steps=8, failing_step_count=1,
        )
        assert a.has_failing_step is True
        assert a.is_assertion_failure is True
        assert "expected 200, got 500" in a.assertion_summary
        assert "click checkout" in a.surgical_recommendation
        assert "Surgical fix" in a.surgical_recommendation  # total > 1

    def test_message_fallback_when_no_expected_actual(self):
        a = build_step_attribution(
            _step(assertion_message="element not visible"),
            total_steps=4, failing_step_count=2,
        )
        assert a.is_assertion_failure is True
        assert a.assertion_summary == "element not visible"

    def test_non_assertion_step_failure(self):
        a = build_step_attribution(_step(name="setup db"), total_steps=3, failing_step_count=1)
        assert a.is_assertion_failure is False
        assert "step 'setup db'" in a.surgical_recommendation

    def test_single_step_phrasing(self):
        a = build_step_attribution(_step(), total_steps=1, failing_step_count=1)
        assert "Surgical fix" not in a.surgical_recommendation
        assert a.surgical_recommendation.startswith("Failing")

    def test_fingerprint_is_stable_and_noise_free(self):
        # Two runs of the "same" step differing only in run-specific numbers must
        # share a fingerprint (deterministic location).
        a1 = build_step_attribution(_step(assertion_trace="at line 42 id=0xAB12"), total_steps=2, failing_step_count=1)
        a2 = build_step_attribution(_step(assertion_trace="at line 99 id=0xFF99"), total_steps=2, failing_step_count=1)
        assert a1.step_fingerprint == a2.step_fingerprint
        assert a1.step_fingerprint  # non-empty

    def test_different_steps_have_different_fingerprints(self):
        a1 = build_step_attribution(_step(name="click checkout"), total_steps=2, failing_step_count=1)
        a2 = build_step_attribution(_step(name="enter address"), total_steps=2, failing_step_count=1)
        assert a1.step_fingerprint != a2.step_fingerprint

    def test_counts_coerced_from_garbage(self):
        a = build_step_attribution(_step(), total_steps="x", failing_step_count=None)
        assert a.total_steps == 0 and a.failing_step_count == 0

    def test_never_raises_on_garbage_step(self):
        for bad in ({"ordinal": object()}, {"expected_value": object()}, {"name": None}):
            a = build_step_attribution(bad, total_steps=2, failing_step_count=1)
            assert isinstance(a, StepFailureAttribution)
            assert a.has_failing_step is True

    def test_assertion_text_is_redacted(self):
        a = build_step_attribution(
            _step(assertion_message="login failed for user test@example.com"),
            total_steps=2, failing_step_count=1,
        )
        assert "test@example.com" not in a.assertion_summary

    def test_to_dict_shape(self):
        d = build_step_attribution(_step(expected_value="a", actual_value="b"), total_steps=2, failing_step_count=1).to_dict()
        assert set(d) == {
            "has_failing_step", "ordinal", "step_name", "keyword",
            "is_assertion_failure", "assertion_summary", "step_fingerprint",
            "total_steps", "failing_step_count", "surgical_recommendation",
        }
