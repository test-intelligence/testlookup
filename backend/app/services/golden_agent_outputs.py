"""
Golden agent-output fixtures (AIQ-P5).

Reference **recorded agent outputs paired with ground truth** that the AIQ-P5
agent-eval harness scores against fixed quality thresholds. Mirrors the style of
``golden_datasets.py``: pure ``get_*`` functions returning ``list[dict]``, a
registry dict, and a builder that materialises ``AgentEvalSample`` instances.

Each entry has the shape::

    {
        "sample_id": str,
        "agent_name": str,
        "recorded_output": {
            "sample_id": str,
            "verdict": str,
            "recommended_actions": [str, ...],
            "agent_contracts": {
                <AgentName>: {
                    "confidence_score": int,
                    "evidence_count": int,
                    "evidence_refs": [...],
                    "decision_reason": str,
                },
            },
        },
        "ground_truth": {"verdict": str, "is_flaky": bool},
    }

The MAIN set (``get_*`` functions registered in ``GOLDEN_AGENT_OUTPUTS``) is
hand-tuned so the harness PASSES every threshold with margin. ``get_negative_
fixtures`` returns entries that DELIBERATELY fail individual metrics and is kept
OUT of the passing set — tests use it to assert metrics CAN fail.
"""
from __future__ import annotations

from app.services.agent_eval_harness import AgentEvalSample


def _refs(n: int) -> list[dict]:
    """Build ``n`` lightweight evidence ref stubs (structural only)."""
    return [{"type": "evidence", "ref_id": f"ev-{i}"} for i in range(n)]


def _entry(
    *,
    sample_id: str,
    agent_name: str,
    verdict: str,
    confidence: int,
    evidence_count: int,
    decision_reason: str,
    recommended_actions: list[str],
    truth_verdict: str,
    is_flaky: bool,
) -> dict:
    """Assemble one recorded-output + ground-truth golden entry."""
    return {
        "sample_id": sample_id,
        "agent_name": agent_name,
        "recorded_output": {
            "sample_id": sample_id,
            "verdict": verdict,
            "recommended_actions": list(recommended_actions),
            "agent_contracts": {
                agent_name: {
                    "confidence_score": confidence,
                    "evidence_count": evidence_count,
                    "evidence_refs": _refs(evidence_count),
                    "decision_reason": decision_reason,
                },
            },
        },
        "ground_truth": {"verdict": truth_verdict, "is_flaky": is_flaky},
    }


def get_golden_analysis_outputs() -> list[dict]:
    """Well-calibrated, correct AnalysisAgent verdicts.

    High-confidence non-flaky bugs with evidence + a remediation action, plus
    flaky verdicts that (correctly) need no action. Verdicts match ground truth
    so outcomes are 1 at high confidence — driving Brier/ECE low.
    """
    return [
        # 3 well-calibrated correct non-flaky (high conf + evidence + action).
        _entry(
            sample_id="ana-product-bug-npe",
            agent_name="AnalysisAgent",
            verdict="product_bug",
            confidence=97,
            evidence_count=3,
            decision_reason="NullPointerException in processRefund; fix the null guard",
            recommended_actions=["Add null check in PaymentService.processRefund"],
            truth_verdict="product_bug",
            is_flaky=False,
        ),
        _entry(
            sample_id="ana-infra-pool",
            agent_name="AnalysisAgent",
            verdict="infrastructure",
            confidence=96,
            evidence_count=2,
            decision_reason="DB connection pool exhausted under load",
            recommended_actions=["Raise pool size and add backpressure"],
            truth_verdict="infrastructure",
            is_flaky=False,
        ),
        _entry(
            sample_id="ana-automation-selector",
            agent_name="AnalysisAgent",
            verdict="automation_defect",
            confidence=95,
            evidence_count=2,
            decision_reason="Stale selector after UI refactor; update the locator",
            recommended_actions=["Update selector to .btn-primary"],
            truth_verdict="automation_defect",
            is_flaky=False,
        ),
        # 2 flaky (no action needed; verdict matches truth).
        _entry(
            sample_id="ana-flaky-race",
            agent_name="AnalysisAgent",
            verdict="flaky",
            confidence=94,
            evidence_count=2,
            decision_reason="Race condition in async handler; passes on retry",
            recommended_actions=[],
            truth_verdict="flaky",
            is_flaky=True,
        ),
        _entry(
            sample_id="ana-flaky-timing",
            agent_name="AnalysisAgent",
            verdict="flaky",
            confidence=92,
            evidence_count=1,
            decision_reason="Timing-dependent assertion under CI load",
            recommended_actions=[],
            truth_verdict="flaky",
            is_flaky=True,
        ),
        # 1 high-conf with evidence (correct).
        _entry(
            sample_id="ana-test-data",
            agent_name="AnalysisAgent",
            verdict="test_data",
            confidence=95,
            evidence_count=2,
            decision_reason="Hard-coded fixture references a deleted account; fix the data",
            recommended_actions=["Reseed staging fixtures"],
            truth_verdict="test_data",
            is_flaky=False,
        ),
        # Extra correct/near-correct samples to keep rate metrics at 1.0 and
        # calibration tight (low confidence on the one miss).
        _entry(
            sample_id="ana-regression",
            agent_name="AnalysisAgent",
            verdict="regression",
            confidence=96,
            evidence_count=3,
            decision_reason="Behaviour changed since last green build; fix the regression",
            recommended_actions=["Revert the offending commit"],
            truth_verdict="regression",
            is_flaky=False,
        ),
        _entry(
            sample_id="ana-infra-dns",
            agent_name="AnalysisAgent",
            verdict="infrastructure",
            confidence=94,
            evidence_count=2,
            decision_reason="DNS resolution failure for external gateway",
            recommended_actions=["Add retry with backoff on resolver"],
            truth_verdict="infrastructure",
            is_flaky=False,
        ),
        # A low-confidence miss: verdict != truth but confidence is low, so the
        # squared error stays small and calibration is preserved.
        _entry(
            sample_id="ana-lowconf-miss",
            agent_name="AnalysisAgent",
            verdict="product_bug",
            confidence=12,
            evidence_count=0,
            decision_reason="Ambiguous timeout signature; fix candidate unclear",
            recommended_actions=["Gather more logs before triage"],
            truth_verdict="infrastructure",
            is_flaky=False,
        ),
    ]


# ── Registry ─────────────────────────────────────────────────────────────────

GOLDEN_AGENT_OUTPUTS: dict[str, dict] = {
    "analysis": {
        "name": "Golden: AnalysisAgent Outputs",
        "description": "Reference recorded AnalysisAgent outputs + ground truth (AIQ-P5)",
        "agent_name": "AnalysisAgent",
        "get_items": get_golden_analysis_outputs,
    },
}


def get_all_golden_agent_outputs() -> list[dict]:
    """Return all registered golden agent-output entries (raw dicts)."""
    entries: list[dict] = []
    for spec in GOLDEN_AGENT_OUTPUTS.values():
        entries.extend(spec["get_items"]())
    return entries


def get_all_golden_agent_samples() -> list[AgentEvalSample]:
    """Materialise the passing golden set into ``AgentEvalSample`` instances."""
    return [
        AgentEvalSample.from_recorded_output(
            entry["recorded_output"],
            agent_name=entry["agent_name"],
            ground_truth=entry["ground_truth"],
        )
        for entry in get_all_golden_agent_outputs()
    ]


def get_negative_fixtures() -> list[dict]:
    """Entries that DELIBERATELY fail individual metrics.

    Kept OUT of the passing set. Tests use these to assert each metric CAN fail:
      * ``neg-highconf-no-evidence`` — high confidence, zero evidence
        (fails completeness).
      * ``neg-flaky-zero-conf`` — flaky verdict at confidence 0
        (fails coherence's flaky-at-zero invariant).
      * ``neg-nonflaky-no-action`` — non-flaky verdict with no action and no fix
        token in the reason (fails actionability).
      * ``neg-overconfident-wrong`` — confident but wrong verdict
        (degrades calibration / Brier).
    """
    return [
        _entry(
            sample_id="neg-highconf-no-evidence",
            agent_name="AnalysisAgent",
            verdict="product_bug",
            confidence=95,
            evidence_count=0,
            decision_reason="Confident bug call with no backing evidence; fix it",
            recommended_actions=["Patch the handler"],
            truth_verdict="product_bug",
            is_flaky=False,
        ),
        _entry(
            sample_id="neg-flaky-zero-conf",
            agent_name="AnalysisAgent",
            verdict="flaky",
            confidence=0,
            evidence_count=0,
            decision_reason="",
            recommended_actions=[],
            truth_verdict="flaky",
            is_flaky=True,
        ),
        _entry(
            sample_id="neg-nonflaky-no-action",
            agent_name="AnalysisAgent",
            verdict="product_bug",
            confidence=70,
            evidence_count=1,
            decision_reason="A bug exists somewhere in the pipeline",
            recommended_actions=[],
            truth_verdict="product_bug",
            is_flaky=False,
        ),
        _entry(
            sample_id="neg-overconfident-wrong",
            agent_name="AnalysisAgent",
            verdict="product_bug",
            confidence=98,
            evidence_count=2,
            decision_reason="Asserted bug with high confidence; fix candidate",
            recommended_actions=["Patch the service"],
            truth_verdict="infrastructure",
            is_flaky=False,
        ),
    ]
