"""Deterministic, report-level quality evaluation.

This module is intentionally pure-local.  It evaluates the already-built
DecisionReport projection; it does not call a model, read a database, or infer
facts that are not present in the signed report.  Optional calibration and
utility metrics are explicitly marked as not evaluated when no corpus or
feedback is supplied.
"""
from __future__ import annotations

from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from app.services.agent_eval_harness import AgentEvalSample, evaluate_agent_outputs

REPORT_EVAL_SCHEMA_VERSION = 1
REPORT_EVAL_THRESHOLDS: dict[str, float] = {
    "citation_validity_min": 0.98,
    "groundedness_min": 0.98,
    "contradiction_rate_max": 0.01,
    "utility_min": 0.80,
    "brier_max": 0.20,
    "ece_max": 0.15,
}
_MATERIAL_KINDS = frozenset({"fact", "inference", "recommendation"})
_POLICY_CONTRADICTION_TOKENS = frozenset({
    "policy_contradiction",
    "release_policy_contradiction",
    "release_policy_conflict",
})


class ReportEvalCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    status: str = Field(pattern=r"^(pass|warn|fail|not_evaluated)$")
    detail: dict[str, Any] = Field(default_factory=dict)


class DecisionReportEvalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = REPORT_EVAL_SCHEMA_VERSION
    status: str = Field(pattern=r"^(pass|warn|fail)$")
    metrics: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, float] = Field(default_factory=lambda: dict(REPORT_EVAL_THRESHOLDS))
    checks: list[ReportEvalCheck] = Field(default_factory=list, max_length=20)
    unavailable_metrics: list[str] = Field(default_factory=list, max_length=20)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _material_claims(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        claim for claim in _as_dict_list(report.get("claims"))
        if str(claim.get("kind") or "") in _MATERIAL_KINDS
    ]


def _reference_id(reference: dict[str, Any]) -> str | None:
    value = reference.get("id") or reference.get("evidence_id") or reference.get("artifact_id")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _citation_metrics(
    report: dict[str, Any],
    authorized_evidence_ids: set[str],
) -> tuple[dict[str, Any], list[str]]:
    claims = _material_claims(report)
    if not claims:
        return {
            "material_claims": 0,
            "cited_claims": 0,
            "valid_citations": 0,
            "citation_validity": None,
            "groundedness": None,
        }, []

    cited = 0
    valid = 0
    invalid: list[str] = []
    for claim in claims:
        references = _as_dict_list(claim.get("evidence"))
        reference_ids = [_reference_id(reference) for reference in references]
        reference_ids = [item for item in reference_ids if item]
        if reference_ids:
            cited += 1
        valid_ids = [item for item in reference_ids if item in authorized_evidence_ids]
        if valid_ids:
            valid += 1
        elif references:
            invalid.append(str(claim.get("claim_id") or "unknown"))
    total = len(claims)
    return {
        "material_claims": total,
        "cited_claims": cited,
        "valid_citations": valid,
        "citation_validity": valid / total,
        "groundedness": cited / total,
    }, sorted(set(invalid))


def _contradiction_metrics(report: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    claims = _material_claims(report)
    contradictions = _as_dict_list(_as_dict(report.get("quality_review")).get("contradictions"))
    policy_conflicts: list[str] = []
    for item in contradictions:
        kind = str(item.get("type") or "").strip().lower()
        if kind in _POLICY_CONTRADICTION_TOKENS:
            policy_conflicts.append(kind)
    denominator = len(claims) or 1
    return {
        "material_claims": len(claims),
        "contradictions": len(contradictions),
        "contradiction_rate": len(contradictions) / denominator if claims else None,
        "policy_contradictions": len(policy_conflicts),
    }, sorted(set(policy_conflicts))


def _utility_metric(feedback_summary: dict[str, Any] | None) -> tuple[float | None, int]:
    summary = _as_dict(feedback_summary)
    sample_count = summary.get("sample_count")
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count <= 0:
        return None, 0
    useful = summary.get("useful_count")
    partial = summary.get("partially_useful_count")
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in (useful, partial)):
        return None, sample_count
    if int(useful) + int(partial) > sample_count:
        return None, sample_count
    return (int(useful) + int(partial)) / sample_count, sample_count


def _action_governance_metric(
    action_summary: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    """Summarize whether persisted proposals reached a governed terminal state."""
    summary = _as_dict(action_summary)
    count = summary.get("action_count")
    unresolved = summary.get("unresolved_count")
    terminal = summary.get("terminal_count")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (count, unresolved, terminal)
    ) or int(unresolved) + int(terminal) != int(count):
        return None, "not_evaluated", {"reason": "action_summary_invalid"}
    resolution_rate = (int(terminal) / int(count)) if int(count) else 1.0
    detail = {
        "action_count": int(count),
        "terminal_count": int(terminal),
        "unresolved_count": int(unresolved),
        "resolution_rate": resolution_rate,
        "status_counts": {
            str(key)[:40]: int(value)
            for key, value in summary.items()
            if key.endswith("_count")
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        },
    }
    return detail, ("pass" if int(unresolved) == 0 else "warn"), detail


def _authorized_ids(report: dict[str, Any], supplied: Iterable[str] | None) -> set[str]:
    ids = {str(item) for item in (supplied or []) if item is not None and str(item)}
    for key in ("run_evidence_bundle_sha256", "evidence_bundle_sha256"):
        if report.get(key):
            ids.add(str(report[key]))
    metric_snapshot = _as_dict(report.get("metric_snapshot"))
    ids.add("metric_snapshot")
    if metric_snapshot.get("content_sha256"):
        ids.add(str(metric_snapshot["content_sha256"]))
    for reference in _as_dict_list(_as_dict(report.get("run_evidence_bundle")).get("evidence_refs")):
        for key in ("evidence_id", "artifact_id"):
            if reference.get(key):
                ids.add(str(reference[key]))
    return ids


def evaluate_decision_report_quality(
    report: dict[str, Any] | None,
    *,
    authorized_evidence_ids: Iterable[str] | None = None,
    eval_samples: Iterable[AgentEvalSample] | None = None,
    feedback_summary: dict[str, Any] | None = None,
    action_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a bounded report-level quality result and never raise.

    Citation/reference failures and explicit policy contradictions are hard
    failures.  Contradiction-rate, calibration, and utility threshold misses
    are warnings in this first rollout slice so existing reports remain
    observable while the agreed pilot corpus is assembled.
    """
    try:
        payload = _as_dict(report)
        authorized = _authorized_ids(payload, authorized_evidence_ids)
        citation, invalid_claims = _citation_metrics(payload, authorized)
        contradiction, policy_conflicts = _contradiction_metrics(payload)
        checks: list[ReportEvalCheck] = []
        unavailable: list[str] = []

        citation_value = citation.get("citation_validity")
        if citation_value is None and not invalid_claims:
            # `citation_validity` is None only when the report has NO material
            # claims -- an empty report, or one whose claims did not parse.
            # This branch used to fall through to the ternary below, where
            # `invalid_claims or (None is not None and ...)` collapses to
            # False, so the check reported "pass": a clean bill of health from
            # a check that never ran, on the one metric this evaluator treats
            # as a HARD failure.
            #
            # Its two siblings immediately below (groundedness,
            # contradiction_rate) already do this correctly, which is what
            # makes the omission unambiguous rather than a design choice. The
            # aggregate reads `not_evaluated` as "warn" and names the metric in
            # `unavailable_metrics`, so an unmeasurable report is now visibly
            # unmeasured instead of quietly green.
            unavailable.append("citation_validity")
            checks.append(ReportEvalCheck(
                name="citation_validity",
                status="not_evaluated",
                detail={"invalid_claim_ids": invalid_claims, **citation},
            ))
        else:
            checks.append(ReportEvalCheck(
                name="citation_validity",
                status=("fail" if invalid_claims or (citation_value is not None and citation_value < REPORT_EVAL_THRESHOLDS["citation_validity_min"]) else "pass"),
                detail={"invalid_claim_ids": invalid_claims, **citation},
            ))
        groundedness = citation.get("groundedness")
        if groundedness is None:
            unavailable.append("groundedness")
            checks.append(ReportEvalCheck(name="groundedness", status="not_evaluated", detail=citation))
        else:
            checks.append(ReportEvalCheck(
                name="groundedness",
                status=("pass" if groundedness >= REPORT_EVAL_THRESHOLDS["groundedness_min"] else "warn"),
                detail=citation,
            ))
        contradiction_rate = contradiction.get("contradiction_rate")
        if contradiction_rate is None:
            unavailable.append("contradiction_rate")
            checks.append(ReportEvalCheck(name="contradiction_rate", status="not_evaluated", detail=contradiction))
        else:
            checks.append(ReportEvalCheck(
                name="contradiction_rate",
                status=("pass" if contradiction_rate <= REPORT_EVAL_THRESHOLDS["contradiction_rate_max"] else "warn"),
                detail=contradiction,
            ))
        checks.append(ReportEvalCheck(
            name="policy_contradictions",
            status="fail" if policy_conflicts else "pass",
            detail={"conflicts": policy_conflicts, **contradiction},
        ))

        samples = [item for item in (eval_samples or []) if isinstance(item, AgentEvalSample)]
        if samples:
            agent_eval = evaluate_agent_outputs(samples).model_dump(mode="json")
            metrics = agent_eval.get("detail", {}).get("calibration", {})
            brier = _safe_float(metrics.get("brier"))
            ece = _safe_float(metrics.get("ece"))
            checks.append(ReportEvalCheck(
                name="calibration",
                status=("pass" if agent_eval.get("passed") else "warn"),
                detail={"agent_eval": agent_eval, "brier": brier, "ece": ece},
            ))
        else:
            unavailable.append("calibration")
            checks.append(ReportEvalCheck(name="calibration", status="not_evaluated"))

        utility, utility_count = _utility_metric(feedback_summary)
        if utility is None:
            unavailable.append("utility")
            checks.append(ReportEvalCheck(name="utility", status="not_evaluated", detail={"sample_count": utility_count}))
        else:
            checks.append(ReportEvalCheck(
                name="utility",
                status=("pass" if utility >= REPORT_EVAL_THRESHOLDS["utility_min"] else "warn"),
                detail={"utility_rate": utility, "sample_count": utility_count},
            ))

        # A caller-supplied corpus has no authoritative action ledger. Keep
        # that optional metric out of the cycle status; authoritative cycles
        # pass an explicit summary (including an all-zero summary).
        if action_summary is not None:
            action_detail, action_status, action_check_detail = _action_governance_metric(
                action_summary
            )
            if action_detail is None:
                unavailable.append("action_governance")
                checks.append(
                    ReportEvalCheck(
                        name="action_governance",
                        status="not_evaluated",
                        detail=action_check_detail,
                    )
                )
            else:
                checks.append(
                    ReportEvalCheck(
                        name="action_governance",
                        status=action_status,
                        detail=action_check_detail,
                    )
                )
        else:
            action_detail = None

        metrics = {**citation, **contradiction}
        if utility is not None:
            metrics["utility_rate"] = utility
        if action_detail is not None:
            metrics.update(
                {
                    "action_count": action_detail["action_count"],
                    "action_resolution_rate": action_detail["resolution_rate"],
                    "action_unresolved_count": action_detail["unresolved_count"],
                }
            )
        status = "fail" if any(item.status == "fail" for item in checks) else (
            "warn" if any(item.status in {"warn", "not_evaluated"} for item in checks) else "pass"
        )
        return DecisionReportEvalResult(
            status=status,
            metrics=metrics,
            checks=checks,
            unavailable_metrics=sorted(set(unavailable)),
        ).model_dump(mode="json")
    except Exception as exc:  # pragma: no cover - defensive contract boundary
        return DecisionReportEvalResult(
            status="fail",
            checks=[ReportEvalCheck(name="evaluator_integrity", status="fail", detail={"error_type": type(exc).__name__})],
        ).model_dump(mode="json")
