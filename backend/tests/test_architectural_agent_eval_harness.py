"""
Architectural / CI-gate ratchet for the report-quality eval harness (AIQ-P5).

Parallel to ``test_architectural_agent_contracts.py`` — this file is the
RELEASE GATE that fails CI if an agent change regresses agent-output quality
(calibration, evidence, actionability, or accuracy). It is pure and DB-free:
it imports the harness + golden fixtures and asserts on computed reports, plus
a couple of static-source invariants (no outbound/DB imports).

The tests fail if:

  1. The golden agent outputs stop clearing every per-metric threshold.
  2. The accuracy metric stops catching a genuinely-wrong (but under-confident)
     agent — i.e. the calibration-only bypass reopens.
  3. The Brier / ECE math drifts from the hand-computed reference values.
  4. A metric is silently dropped from ``PASS_THRESHOLDS``.
  5. The harness gains an outbound HTTP / LLM / DB import (offline invariant).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.agent_eval_harness import (
    MIN_SAMPLES,
    NON_FLAKY_VERDICTS,
    PASS_THRESHOLDS,
    AgentEvalReport,
    AgentEvalSample,
    evaluate_agent_outputs,
)
from app.services.golden_agent_outputs import (
    get_all_golden_agent_samples,
    get_negative_fixtures,
    get_wrong_agent_samples,
)

HARNESS_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "agent_eval_harness.py"
)


# ── The gate ─────────────────────────────────────────────────────────────────


def test_golden_agent_outputs_meet_report_quality_thresholds() -> None:
    """THE GATE: the golden agent outputs must clear EVERY metric threshold.

    If an agent change regresses calibration / evidence / actions / accuracy,
    the golden set stops passing and this test fails CI.
    """
    report = evaluate_agent_outputs(get_all_golden_agent_samples())

    assert report.passed is True, (
        "Golden agent outputs no longer pass the report-quality gate: "
        f"{report.per_metric_pass} detail={report.detail}"
    )
    for metric, passed in report.per_metric_pass.items():
        assert passed is True, (
            f"Golden agent outputs regressed metric '{metric}'. "
            f"per_metric_pass={report.per_metric_pass}"
        )


def test_wrong_agent_fails_gate() -> None:
    """An under-confident, always-wrong agent must FAIL the gate, and the
    failure must come from the accuracy metric — proving accuracy closes the
    calibration-only bypass (low confidence keeps Brier/ECE tolerable)."""
    report = evaluate_agent_outputs(get_wrong_agent_samples())

    assert report.passed is False
    assert report.per_metric_pass["accuracy"] is False, (
        "The accuracy metric must reject an always-wrong agent even when it is "
        f"appropriately under-confident: {report.per_metric_pass}"
    )
    assert report.accuracy == 0.0


# ── Calibration math ─────────────────────────────────────────────────────────


def test_brier_math() -> None:
    """Hand-computed Brier over 4 samples.

    p = conf/100, outcome = 1 if verdict == ground_truth else 0,
    Brier = mean((p - outcome)^2):
      conf 100 correct -> (1.0 - 1)^2 = 0.00
      conf  80 correct -> (0.8 - 1)^2 = 0.04
      conf  90 wrong   -> (0.9 - 0)^2 = 0.81
      conf   0 wrong   -> (0.0 - 0)^2 = 0.00
      Brier = (0.00 + 0.04 + 0.81 + 0.00) / 4 = 0.2125
    """
    samples = [
        AgentEvalSample(
            confidence_score=100, verdict="product_bug", ground_truth_verdict="product_bug"
        ),
        AgentEvalSample(
            confidence_score=80, verdict="product_bug", ground_truth_verdict="product_bug"
        ),
        AgentEvalSample(
            confidence_score=90, verdict="product_bug", ground_truth_verdict="infrastructure"
        ),
        AgentEvalSample(
            confidence_score=0, verdict="flaky", ground_truth_verdict="product_bug"
        ),
    ]
    report = evaluate_agent_outputs(samples)
    assert report.brier == pytest.approx(0.2125)


def test_ece_math() -> None:
    """Hand-computed ECE spanning bin 1 and bin 9 (incl conf=100 in bin 9).

    bin index = min(ECE_BINS - 1, int(p * ECE_BINS)).
      bin 1 (conf 10): one wrong (outcome 0) + one correct (outcome 1)
            -> count 2, acc 0.5, conf 0.10, |acc - conf| = 0.40, weight 2/4
      bin 9 (conf 100 + conf 95, both correct)
            -> count 2, acc 1.0, conf 0.975, |acc - conf| = 0.025, weight 2/4
      ECE = 0.5 * 0.40 + 0.5 * 0.025 = 0.2125

    The conf=100 sample pins bin-9 membership (no off-by-one overflow to a
    tenth, non-existent bin).
    """
    samples = [
        AgentEvalSample(confidence_score=10, verdict="a", ground_truth_verdict="b"),
        AgentEvalSample(confidence_score=10, verdict="a", ground_truth_verdict="a"),
        AgentEvalSample(confidence_score=100, verdict="a", ground_truth_verdict="a"),
        AgentEvalSample(confidence_score=95, verdict="a", ground_truth_verdict="a"),
    ]
    report = evaluate_agent_outputs(samples)
    assert report.ece == pytest.approx(0.2125)

    # The conf=100 sample lands in bin 9, not an overflow bin 10.
    bins = {b["bin"]: b for b in report.detail["calibration"]["bins"]}
    assert 9 in bins
    assert bins[9]["count"] == 2


# ── Individual metric gate behaviour ─────────────────────────────────────────


def test_completeness_fails_when_high_conf_missing_evidence() -> None:
    """A high-confidence (>= CONF_EVIDENCE_FLOOR) sample with zero evidence must
    drag completeness below its 0.90 threshold."""
    samples = [
        AgentEvalSample(
            confidence_score=95,
            verdict="product_bug",
            ground_truth_verdict="product_bug",
            evidence_count=0,
            decision_reason="confident but no evidence; fix it",
            recommended_actions=["patch"],
        ),
    ]
    report = evaluate_agent_outputs(samples)
    assert report.completeness < 0.90
    assert report.per_metric_pass["completeness"] is False


def test_actionability_excludes_flaky() -> None:
    """An all-flaky sample set (no verdict in NON_FLAKY_VERDICTS) has an empty
    actionability denominator and scores 1.0 — flaky verdicts are never
    required to carry a remediation action."""
    samples = [
        AgentEvalSample(
            confidence_score=90,
            verdict="flaky",
            ground_truth_verdict="flaky",
            evidence_count=1,
            decision_reason="race condition; passes on retry",
            recommended_actions=[],
        )
        for _ in range(5)
    ]
    assert all(s.verdict not in NON_FLAKY_VERDICTS for s in samples)
    report = evaluate_agent_outputs(samples)
    assert report.actionability == 1.0


def test_accuracy_metric() -> None:
    """7/10 correct passes the 0.70 accuracy threshold; 6/10 fails."""

    def _samples(correct: int, total: int) -> list[AgentEvalSample]:
        out: list[AgentEvalSample] = []
        for i in range(total):
            hit = i < correct
            out.append(
                AgentEvalSample(
                    confidence_score=50,
                    verdict="product_bug",
                    ground_truth_verdict="product_bug" if hit else "infrastructure",
                )
            )
        return out

    passing = evaluate_agent_outputs(_samples(7, 10))
    assert passing.accuracy == pytest.approx(0.70)
    assert passing.per_metric_pass["accuracy"] is True

    failing = evaluate_agent_outputs(_samples(6, 10))
    assert failing.accuracy == pytest.approx(0.60)
    assert failing.per_metric_pass["accuracy"] is False


def test_insufficient_data_fails() -> None:
    """Empty input and any sub-MIN_SAMPLES set must NOT pass, even when the
    per-metric checks are vacuously satisfied."""
    empty = evaluate_agent_outputs([])
    assert empty.passed is False
    assert empty.sample_count == 0
    assert empty.detail.get("insufficient_data") is True

    # Fewer than MIN_SAMPLES but otherwise perfectly-scoring samples.
    few = [
        AgentEvalSample(
            confidence_score=90,
            verdict="product_bug",
            ground_truth_verdict="product_bug",
            evidence_count=2,
            decision_reason="bug found; fix it",
            recommended_actions=["patch"],
        )
        for _ in range(MIN_SAMPLES - 1)
    ]
    report = evaluate_agent_outputs(few)
    assert report.sample_count == MIN_SAMPLES - 1
    assert report.passed is False
    assert report.detail.get("insufficient_data") is True


def test_harness_never_raises_on_garbage() -> None:
    """The never-raise invariant: weird construction + malformed recorded
    outputs must never raise, and the documented coercions must hold."""
    # Direct construction with weird field values does not raise.
    weird = AgentEvalSample(
        sample_id=123,
        agent_name=None,
        verdict=None,
        confidence_score="high",
        evidence_count="not-a-number",
        decision_reason=None,
        recommended_actions=None,
        ground_truth_verdict=None,
    )
    assert isinstance(weird, AgentEvalSample)
    assert weird.confidence_score == 0
    assert weird.recommended_actions == []

    # from_recorded_output with broken shapes: missing agent_contracts,
    # evidence_refs as a string, recommended_actions None/int, verdict None.
    malformed_payloads = [
        None,
        {},
        {"verdict": None, "recommended_actions": None},
        {"verdict": "product_bug", "recommended_actions": 5},
        {
            "verdict": "flaky",
            "agent_contracts": {"A": {"evidence_refs": "not-a-list", "confidence_score": "x"}},
        },
        {"agent_contracts": "not-a-dict"},
        12345,
    ]
    built = [
        AgentEvalSample.from_recorded_output(p, agent_name="A", ground_truth=None)
        for p in malformed_payloads
    ]
    for s in built:
        assert isinstance(s, AgentEvalSample)

    report = evaluate_agent_outputs(built)
    assert isinstance(report, AgentEvalReport)

    # Documented confidence coercions (via field_validator mode="before").
    assert AgentEvalSample(confidence_score="90.5").confidence_score == 90
    assert AgentEvalSample(confidence_score="85").confidence_score == 85
    assert AgentEvalSample(confidence_score="abc").confidence_score == 0
    assert AgentEvalSample(confidence_score=250).confidence_score == 100
    assert AgentEvalSample(confidence_score=-10).confidence_score == 0
    assert AgentEvalSample(confidence_score=None).confidence_score == 0
    assert AgentEvalSample(confidence_score=float("inf")).confidence_score == 0
    assert AgentEvalSample(confidence_score=float("nan")).confidence_score == 0


def test_negative_fixtures_can_fail_metrics() -> None:
    """The negative fixtures (kept OUT of the passing set) must fail the gate —
    they deliberately break individual metrics."""
    samples = [
        AgentEvalSample.from_recorded_output(
            entry["recorded_output"],
            agent_name=entry["agent_name"],
            ground_truth=entry["ground_truth"],
        )
        for entry in get_negative_fixtures()
    ]
    report = evaluate_agent_outputs(samples)
    assert report.passed is False


# ── Ratchet floors ───────────────────────────────────────────────────────────


def test_thresholds_present() -> None:
    """PASS_THRESHOLDS must expose every metric key — so a metric can't be
    silently dropped from the gate."""
    required = {
        "coherence",
        "completeness",
        "actionability",
        "accuracy",
        "brier_max",
        "ece_max",
    }
    assert required <= set(PASS_THRESHOLDS), (
        "PASS_THRESHOLDS is missing gate metric keys: "
        f"{sorted(required - set(PASS_THRESHOLDS))}"
    )


def test_offline_no_outbound_imports() -> None:
    """The harness must stay pure-local: no HTTP / LLM / DB session imports.

    Pins the OFFLINE + no-DB invariant by static source inspection.
    """
    source = HARNESS_SOURCE.read_text(encoding="utf-8")
    forbidden = [
        "httpx",
        "requests",
        "openai",
        "anthropic",
        "ollama",
        "llm_factory",
        "AsyncSession",
    ]
    offenders = [name for name in forbidden if name in source]
    assert not offenders, (
        "agent_eval_harness.py must not import outbound/DB clients "
        f"(found references to: {offenders})"
    )
