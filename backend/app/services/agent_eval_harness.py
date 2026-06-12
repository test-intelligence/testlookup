"""
Pure-local agent-output evaluation harness (AIQ-P5).

This module is ADDITIVE and pure-local. It scores **recorded agent outputs**
against ground truth along four axes — coherence, completeness, actionability,
and calibration (Brier / ECE) — and folds them into a single ``AgentEvalReport``
with per-metric pass/fail against fixed thresholds.

Like the rest of the AIQ helpers it NEVER performs outbound calls, NEVER reads
or writes a database, NEVER imports an LLM/HTTP client, NEVER mutates its
inputs, and — by hard invariant — NEVER raises on any input shape. All coercion
lives in ``field_validator(mode="before")`` so a malformed field degrades to its
default rather than crashing, and ``evaluate_agent_outputs`` wraps its body so an
unexpected error degrades to a failed report instead of propagating.
"""
from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = structlog.get_logger("services.agent_eval_harness")

# Per-metric pass thresholds. Rate metrics are higher-is-better; brier/ece are
# lower-is-better error measures with a hard ceiling.
PASS_THRESHOLDS: dict[str, float] = {
    "coherence": 0.90,
    "completeness": 0.90,
    "actionability": 0.85,
    "brier_max": 0.20,
    "ece_max": 0.15,
}
# Confidence at/above which a sample is expected to cite at least one evidence.
CONF_EVIDENCE_FLOOR = 60
# Verdicts that demand a remediation action (everything but pure "flaky").
NON_FLAKY_VERDICTS = frozenset(
    {"product_bug", "infrastructure", "automation_defect", "test_data", "regression"}
)
# Equal-width bins used for the Expected Calibration Error.
ECE_BINS = 10
# Minimum sample count before a report is allowed to PASS.
MIN_SAMPLES = 5


def _as_list(value) -> list:
    """Coerce a value to a list, returning [] for any non-collection input."""
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


class AgentEvalSample(BaseModel):
    """A single recorded agent output paired with its ground truth."""

    model_config = ConfigDict(extra="ignore")

    sample_id: str = ""
    agent_name: str = ""
    verdict: str = ""
    confidence_score: int = 0
    evidence_count: int = 0
    decision_reason: str = ""
    recommended_actions: list[str] = Field(default_factory=list)
    ground_truth_verdict: str = ""
    is_flaky_truth: bool = False

    @field_validator("sample_id", "agent_name", "decision_reason", mode="before")
    @classmethod
    def _coerce_str(cls, value) -> str:
        if value is None:
            return ""
        return str(value)

    @field_validator("verdict", "ground_truth_verdict", mode="before")
    @classmethod
    def _coerce_verdict(cls, value) -> str:
        if value is None:
            return ""
        try:
            return str(value).strip().lower()
        except Exception:
            return ""

    @field_validator("confidence_score", mode="before")
    @classmethod
    def _clamp_confidence(cls, value) -> int:
        # OverflowError guards float('inf')/-inf; bare Exception mirrors the
        # evidence module so no input shape can crash construction.
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        return max(0, min(100, number))

    @field_validator("evidence_count", mode="before")
    @classmethod
    def _coerce_evidence_count(cls, value) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        return max(0, number)

    @field_validator("recommended_actions", mode="before")
    @classmethod
    def _coerce_actions(cls, value) -> list[str]:
        if value is None:
            return []
        # A bare string is a single action, not an iterable of characters.
        if isinstance(value, str):
            return [value]
        if not isinstance(value, (list, tuple, set)):
            return []
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            out.append(str(item))
        return out

    @classmethod
    def from_recorded_output(
        cls,
        payload: dict,
        *,
        agent_name: str,
        ground_truth: dict,
    ) -> "AgentEvalSample":
        """Build a sample from a recorded-output payload + ground truth.

        Defensively extracts ``payload["agent_contracts"][agent_name]`` for the
        per-agent contract fields and payload-level verdict / recommended
        actions. Missing keys degrade to defaults; NEVER raises.
        """
        payload = payload if isinstance(payload, dict) else {}
        ground_truth = ground_truth if isinstance(ground_truth, dict) else {}

        contracts = payload.get("agent_contracts")
        contracts = contracts if isinstance(contracts, dict) else {}
        contract = contracts.get(agent_name)
        contract = contract if isinstance(contract, dict) else {}

        evidence_refs = _as_list(contract.get("evidence_refs"))
        evidence_count = contract.get("evidence_count")
        if evidence_count is None and evidence_refs:
            evidence_count = len(evidence_refs)

        return cls(
            sample_id=payload.get("sample_id", ""),
            agent_name=agent_name,
            verdict=payload.get("verdict", ""),
            confidence_score=contract.get("confidence_score", 0),
            evidence_count=evidence_count if evidence_count is not None else 0,
            decision_reason=contract.get("decision_reason", ""),
            recommended_actions=payload.get("recommended_actions", []),
            ground_truth_verdict=ground_truth.get("verdict", ""),
            is_flaky_truth=bool(ground_truth.get("is_flaky", False)),
        )


# ── Metric functions ─────────────────────────────────────────────────────────


def score_coherence(samples: list[AgentEvalSample]) -> dict:
    """Per-sample internal-coherence invariants.

    Each sample must satisfy: confidence in [0, 100]; NOT (verdict=="flaky" and
    confidence_score==0); decision_reason non-empty when confidence_score>0.
    Empty input scores 1.0.
    """
    samples = [s for s in _as_list(samples) if isinstance(s, AgentEvalSample)]
    total = len(samples)
    if total == 0:
        return {"score": 1.0, "violations": [], "total": 0}

    violations: list[dict] = []
    for s in samples:
        reasons: list[str] = []
        if not (0 <= s.confidence_score <= 100):
            reasons.append("confidence_out_of_range")
        if s.verdict == "flaky" and s.confidence_score == 0:
            reasons.append("flaky_at_zero_confidence")
        if s.confidence_score > 0 and not s.decision_reason:
            reasons.append("missing_decision_reason")
        if reasons:
            violations.append({"sample_id": s.sample_id, "reasons": reasons})

    passed = total - len(violations)
    return {"score": passed / total, "violations": violations, "total": total}


def score_completeness(samples: list[AgentEvalSample]) -> dict:
    """High-confidence samples must cite evidence.

    Among samples with confidence_score >= CONF_EVIDENCE_FLOOR, require
    evidence_count >= 1. Empty denominator scores 1.0.
    """
    samples = [s for s in _as_list(samples) if isinstance(s, AgentEvalSample)]
    high_conf = [s for s in samples if s.confidence_score >= CONF_EVIDENCE_FLOOR]
    denom = len(high_conf)
    with_evidence = sum(1 for s in high_conf if s.evidence_count >= 1)
    score = (with_evidence / denom) if denom else 1.0
    return {"score": score, "high_conf": denom, "with_evidence": with_evidence}


def score_actionability(samples: list[AgentEvalSample]) -> dict:
    """Non-flaky verdicts must carry a remediation path.

    Among samples whose verdict is in NON_FLAKY_VERDICTS, require at least one
    recommended action OR a fix token in the decision_reason. Empty denominator
    scores 1.0.
    """
    samples = [s for s in _as_list(samples) if isinstance(s, AgentEvalSample)]
    non_flaky = [s for s in samples if s.verdict in NON_FLAKY_VERDICTS]
    denom = len(non_flaky)
    with_action = 0
    for s in non_flaky:
        has_action = len(s.recommended_actions) >= 1
        has_fix_token = "fix" in s.decision_reason.lower()
        if has_action or has_fix_token:
            with_action += 1
    score = (with_action / denom) if denom else 1.0
    return {"score": score, "non_flaky": denom, "with_action": with_action}


def score_calibration(samples: list[AgentEvalSample]) -> dict:
    """Brier score + Expected Calibration Error over the sample set.

    p_i = confidence_score/100; outcome_i = 1 if verdict == ground_truth_verdict
    (both already lowercased) else 0. Brier = mean((p - outcome)^2). ECE over
    ECE_BINS equal-width bins on [0, 1]: per bin acc_b = mean(outcome|S_b),
    conf_b = mean(p|S_b), weight = |S_b|/N, ECE = sum weight*|acc - conf|.
    Empty input yields brier/ece None.
    """
    samples = [s for s in _as_list(samples) if isinstance(s, AgentEvalSample)]
    n = len(samples)
    if n == 0:
        return {"brier": None, "ece": None, "n": 0, "bins": []}

    probs: list[float] = []
    outcomes: list[int] = []
    for s in samples:
        probs.append(s.confidence_score / 100.0)
        outcomes.append(1 if s.verdict == s.ground_truth_verdict else 0)

    brier = sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / n

    # Bin membership: bin index floor(p * BINS), clamped so p==1.0 lands in the
    # last bin rather than overflowing to index BINS.
    bin_members: list[list[int]] = [[] for _ in range(ECE_BINS)]
    for idx, p in enumerate(probs):
        b = min(ECE_BINS - 1, int(p * ECE_BINS))
        bin_members[b].append(idx)

    ece = 0.0
    bins_detail: list[dict] = []
    for b, members in enumerate(bin_members):
        count = len(members)
        if count == 0:
            continue
        acc_b = sum(outcomes[i] for i in members) / count
        conf_b = sum(probs[i] for i in members) / count
        weight = count / n
        ece += weight * abs(acc_b - conf_b)
        bins_detail.append({
            "bin": b,
            "count": count,
            "accuracy": round(acc_b, 4),
            "confidence": round(conf_b, 4),
        })

    return {
        "brier": round(brier, 4),
        "ece": round(ece, 4),
        "n": n,
        "bins": bins_detail,
    }


# ── Report ───────────────────────────────────────────────────────────────────


class AgentEvalReport(BaseModel):
    """Aggregated agent-output quality report with per-metric pass/fail."""

    model_config = ConfigDict(extra="ignore")

    coherence: float | None = None
    completeness: float | None = None
    actionability: float | None = None
    brier: float | None = None
    ece: float | None = None
    sample_count: int = 0
    per_metric_pass: dict[str, bool] = Field(default_factory=dict)
    passed: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)


def _failed_report(sample_count: int, detail: dict) -> AgentEvalReport:
    """A fully-failed report used as the never-raise fallback."""
    return AgentEvalReport(
        coherence=None,
        completeness=None,
        actionability=None,
        brier=None,
        ece=None,
        sample_count=sample_count,
        per_metric_pass={
            "coherence": False,
            "completeness": False,
            "actionability": False,
            "brier": False,
            "ece": False,
        },
        passed=False,
        detail=detail,
    )


def evaluate_agent_outputs(samples: list[AgentEvalSample]) -> AgentEvalReport:
    """Run the four metrics, apply thresholds, and assemble a report.

    Higher-is-better metrics pass when ``>=`` their threshold; brier passes when
    ``<= brier_max``; ece when ``<= ece_max``. A ``None`` metric (empty
    denominator / no samples) passes vacuously. ``passed`` requires every
    per-metric pass AND sample_count >= MIN_SAMPLES. NEVER raises — on an
    unexpected error it logs and returns a failed report.
    """
    try:
        valid = [s for s in _as_list(samples) if isinstance(s, AgentEvalSample)]
        sample_count = len(valid)

        coh = score_coherence(valid)
        comp = score_completeness(valid)
        act = score_actionability(valid)
        cal = score_calibration(valid)

        coherence = coh["score"]
        completeness = comp["score"]
        actionability = act["score"]
        brier = cal["brier"]
        ece = cal["ece"]

        per_metric_pass = {
            "coherence": coherence is None or coherence >= PASS_THRESHOLDS["coherence"],
            "completeness": completeness is None
            or completeness >= PASS_THRESHOLDS["completeness"],
            "actionability": actionability is None
            or actionability >= PASS_THRESHOLDS["actionability"],
            "brier": brier is None or brier <= PASS_THRESHOLDS["brier_max"],
            "ece": ece is None or ece <= PASS_THRESHOLDS["ece_max"],
        }

        detail: dict[str, Any] = {
            "coherence": coh,
            "completeness": comp,
            "actionability": act,
            "calibration": cal,
            "thresholds": dict(PASS_THRESHOLDS),
        }
        if sample_count < MIN_SAMPLES:
            detail["insufficient_data"] = True

        passed = all(per_metric_pass.values()) and sample_count >= MIN_SAMPLES

        return AgentEvalReport(
            coherence=coherence,
            completeness=completeness,
            actionability=actionability,
            brier=brier,
            ece=ece,
            sample_count=sample_count,
            per_metric_pass=per_metric_pass,
            passed=passed,
            detail=detail,
        )
    except Exception as exc:  # never-raise invariant
        logger.warning("agent_eval_report_failed", error=str(exc))
        return _failed_report(0, {"error": str(exc)})
