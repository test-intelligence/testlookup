"""FLK-P4 — flaky-investigation reasoning (pure, no-DB, never-raise).

The agentic flaky investigator confirms a flaky verdict and *explains* it. This
module holds the pure reasoning so it is unit-testable without a DB and reusable
from both the LangGraph agent (`flaky_sentinel_agent`) and the read-time
surfaces (`/flaky-coach`, `/failures`):

  * ``cluster_failures`` — group a per-run window by error signature + stack
    fingerprint (FLK-P1 helpers) into a small, explainable cluster summary.
  * ``determine_likely_cause`` — map the FLK-P1 intermittency signals (+ the
    optional FLK-P3 ML confidence) to a human cause string and a stable code.
  * ``build_flaky_verdict`` — assemble the structured ``{is_flaky, confidence,
    likely_cause, evidence[]}`` verdict, where ``confidence`` is the AIQ-P3
    evidence-weighted aggregate (capped, auditable breakdown).

Like the other FLK/AIQ helpers it performs NO outbound calls, NO DB I/O, and
NEVER raises on any input shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from app.agents.evidence import EvidenceRef, aggregate_confidence
from app.services.flaky_signals import (
    IntermittencySignals,
    error_signature,
    stack_fingerprint,
)

_FAILED = {"FAILED", "BROKEN"}

# ML confidence bands (mirror the FLK-P3 advisory thresholds in the coach).
_ML_CONFIRM = 0.70
_ML_SKEPTIC = 0.30


def _norm_status(value: Any) -> str:
    try:
        text = str(value).upper().strip()
    except Exception:
        return "UNKNOWN"
    return text.rsplit(".", 1)[-1] if "." in text else text


@dataclass(frozen=True)
class FailureClusters:
    """Explainable clustering of a test's recent failures."""

    total_failures: int = 0
    distinct_error_signatures: int = 0
    distinct_stack_fingerprints: int = 0
    dominant_error: str = ""
    dominant_error_count: int = 0
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_failures": self.total_failures,
            "distinct_error_signatures": self.distinct_error_signatures,
            "distinct_stack_fingerprints": self.distinct_stack_fingerprints,
            "dominant_error": self.dominant_error,
            "dominant_error_count": self.dominant_error_count,
            "examples": list(self.examples),
        }


def cluster_failures(records: Iterable[Mapping[str, Any]]) -> FailureClusters:
    """Cluster the FAILED rows of a per-run window by error signature + stack
    fingerprint. Never raises — malformed rows are skipped.
    """
    rows = [r for r in (records or []) if isinstance(r, Mapping)]
    error_counts: dict[str, int] = {}
    stacks: set[str] = set()
    examples: list[str] = []
    total_failures = 0
    for r in rows:
        if _norm_status(r.get("status")) not in _FAILED:
            continue
        total_failures += 1
        sig = error_signature(r.get("error_message"))
        if sig:
            error_counts[sig] = error_counts.get(sig, 0) + 1
            if sig not in examples and len(examples) < 3:
                examples.append(sig)
        fp = stack_fingerprint(r.get("stack_trace"))
        if fp:
            stacks.add(fp)
    dominant_error, dominant_count = "", 0
    if error_counts:
        dominant_error, dominant_count = max(error_counts.items(), key=lambda kv: kv[1])
    return FailureClusters(
        total_failures=total_failures,
        distinct_error_signatures=len(error_counts),
        distinct_stack_fingerprints=len(stacks),
        dominant_error=dominant_error,
        dominant_error_count=dominant_count,
        examples=examples,
    )


def determine_likely_cause(
    signals: IntermittencySignals,
    ml_confidence: Optional[float] = None,
) -> tuple[str, str]:
    """Map intermittency signals (+ optional ML confidence) to a
    ``(human_text, stable_code)`` likely-cause. Never raises.
    """
    label = getattr(signals, "intermittency_label", "insufficient_data")

    if label == "insufficient_data":
        return ("Insufficient history to attribute a cause — keep monitoring.", "insufficient_data")

    # The ML model (FLK-P3) is the strongest single signal at its extremes: a
    # confident "not a flake" overrides the heuristics.
    if ml_confidence is not None and ml_confidence <= _ML_SKEPTIC:
        return (
            "Likely a real failure, not a flake — the ML model trained on past "
            "quarantine decisions is skeptical of the flaky verdict.",
            "likely_regression",
        )

    if getattr(signals, "in_run_retry_rate", 0.0) > 0.0:
        return (
            "Framework-confirmed in-run retry flake — the test passed on retry "
            "within a single run, so non-determinism is internal to the test.",
            "in_run_retry",
        )
    if label == "environmental_flaky":
        return (
            "Environmental / infrastructure noise — many distinct error "
            "signatures across runs (network, resource limits, shared state).",
            "environmental",
        )
    if getattr(signals, "stack_trace_diversity", 0.0) >= 0.5:
        return (
            "Concurrency / race condition — the same test fails at many distinct "
            "stack locations, pointing to timing rather than a single bug.",
            "race_condition",
        )
    if label == "persistent_regression":
        return (
            "Low volatility with a single repeated error — looks like a real "
            "regression, not a flake. Investigate as a bug.",
            "likely_regression",
        )
    if label == "intermittent_flaky":
        return (
            "Intermittent flake — flips pass↔fail; check timing, ordering, and "
            "shared-state dependencies.",
            "intermittent",
        )
    if label == "low_volatility_flaky":
        return (
            "Emerging / low-volatility flake — gather more runs to confirm before "
            "quarantining.",
            "low_volatility",
        )
    return ("Indeterminate flakiness pattern.", "indeterminate")


def _ref(source: str, ref_id: str, excerpt: str, strength: str, contribution: int) -> EvidenceRef:
    return EvidenceRef(
        source=source, ref_id=ref_id, excerpt=excerpt,
        strength=strength, contribution=contribution,
    )


def build_flaky_verdict(
    failure_rate: float,
    signals: IntermittencySignals,
    *,
    ml_confidence: Optional[float] = None,
    wilson: Any = None,
    clusters: Optional[FailureClusters] = None,
    build_change_summary: Optional[str] = None,
) -> dict:
    """Assemble the structured FLK-P4 verdict.

    Returns ``{is_flaky, confidence, likely_cause, likely_cause_code,
    evidence, confidence_breakdown}``. ``confidence`` is the AIQ-P3
    evidence-weighted aggregate so it carries an auditable breakdown and the
    "high confidence needs strong evidence" cap. Never raises.
    """
    cause_text, cause_code = determine_likely_cause(signals, ml_confidence)

    refs: list[EvidenceRef] = []

    vol = float(getattr(signals, "status_volatility", 0.0) or 0.0)
    flips = int(getattr(signals, "flip_count", 0) or 0)
    runs = int(getattr(signals, "runs", 0) or 0)
    refs.append(_ref(
        "intermittency", "status_volatility",
        f"flips {flips}x over {runs} runs (volatility {round(vol, 3)})",
        "medium" if vol >= 0.4 else "weak",
        round(min(1.0, max(0.0, vol)) * 100),
    ))

    err_div = float(getattr(signals, "error_signature_diversity", 0.0) or 0.0)
    if clusters and clusters.total_failures:
        refs.append(_ref(
            "error_clustering", "error_signature_diversity",
            f"{clusters.distinct_error_signatures} distinct error signature(s) "
            f"over {clusters.total_failures} failures; dominant ×{clusters.dominant_error_count}",
            "medium" if err_div >= 0.5 else "weak",
            round(min(1.0, max(0.0, err_div)) * 100),
        ))

    stack_div = float(getattr(signals, "stack_trace_diversity", 0.0) or 0.0)
    if stack_div > 0.0:
        refs.append(_ref(
            "stack_clustering", "stack_trace_diversity",
            f"stack-trace diversity {round(stack_div, 3)} "
            f"({'many distinct traces — race/env' if stack_div >= 0.5 else 'mostly one trace'})",
            "medium" if stack_div >= 0.5 else "weak",
            round(stack_div * 100),
        ))

    retry = float(getattr(signals, "in_run_retry_rate", 0.0) or 0.0)
    if retry > 0.0:
        refs.append(_ref(
            "framework_retry", "in_run_retry_rate",
            f"framework recorded in-run retries on {round(retry * 100)}% of runs",
            "strong", round(min(1.0, retry) * 100),
        ))

    if ml_confidence is not None:
        at_extreme = ml_confidence >= _ML_CONFIRM or ml_confidence <= _ML_SKEPTIC
        refs.append(_ref(
            "ml_model", "is_flaky_confidence",
            f"ML flakiness confidence {round(ml_confidence * 100)}% "
            "(learned from quarantine decisions)",
            "strong" if at_extreme else "medium",
            round(min(1.0, max(0.0, ml_confidence)) * 100),
        ))

    if wilson is not None:
        low = float(getattr(wilson, "low", 0.0) or 0.0)
        high = float(getattr(wilson, "high", 0.0) or 0.0)
        total = int(getattr(wilson, "total", 0) or 0)
        refs.append(_ref(
            "statistics", "wilson_ci",
            f"failure rate 95% CI [{round(low * 100)}%, {round(high * 100)}%] over {total} runs",
            "medium" if total >= 20 else "weak",
            round(min(1.0, max(0.0, low)) * 100),
        ))

    if build_change_summary and build_change_summary not in (
        "Build change lookup skipped.", "No changes found.",
    ):
        refs.append(_ref(
            "build_metadata", "build_changes", build_change_summary, "weak", 40,
        ))

    confidence, breakdown = aggregate_confidence(refs)

    # is_flaky reflects the cause: a confirmed-regression / insufficient-data
    # verdict is NOT a flake; every other cause is.
    is_flaky = cause_code not in {"likely_regression", "insufficient_data"}

    return {
        "is_flaky": is_flaky,
        "confidence": confidence,
        "likely_cause": cause_text,
        "likely_cause_code": cause_code,
        "evidence": [r.as_legacy_dict() for r in refs],
        "confidence_breakdown": breakdown,
    }
