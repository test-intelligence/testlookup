"""AI agent for grounded two-run comparison reports."""
from __future__ import annotations

import json
from typing import Any

import structlog

from app.models.agent_contracts import RunCompareAgentOutput, validate_agent_contract
from app.services.llm_json_parser import parse_llm_json
from app.services.prompt_registry import get_prompt_text

logger = structlog.get_logger("agents.run_compare")

#: Part of ``uq_run_comparison_report_scope`` and of ``run_compare_ai_service._get_row``'s
#: filter, so v2 reports (they carry ``ai_changes``, AIH-03) never serve a cached v1 row.
#: The prompt TEXT is unchanged (W03 contract 0.12): the AI-analysis changes ride in the
#: LLM context as plain data, and ``ai_changes`` is attached deterministically after
#: parsing, whatever the model returned.
PROMPT_VERSION = "run_compare_v2"

#: ``ai_analysis_changes`` in the LLM context keeps at most this many test changes.
_CONTEXT_TEST_CHANGES = 10

# Prompt texts live in the prompt registry (AI-F2) — edit there, with a
# manifest bump + eval-gate attestation.
_SYSTEM_PROMPT = get_prompt_text("run_compare_system")

_REPORT_PROMPT = get_prompt_text("run_compare_report")


class RunCompareAgent:
    """Small, stateless agent that narrates a deterministic run diff."""

    async def generate(self, compare_payload: dict[str, Any]) -> dict[str, Any]:
        context = _build_context(compare_payload)
        fallback = build_fallback_report(compare_payload)
        try:
            from app.services.llm_factory import get_llm

            llm = await get_llm(temperature=0.0)
            response = await llm.ainvoke(
                _REPORT_PROMPT.format(
                    system=_SYSTEM_PROMPT,
                    context=json.dumps(context, default=str, ensure_ascii=True),
                )
            )
            raw = getattr(response, "content", str(response))
            parsed, err = parse_llm_json(
                raw,
                expected_keys=[
                    "executive_summary",
                    "risk_level",
                    "key_differences",
                    "new_risks",
                    "resolved_risks",
                    "duration_concerns",
                    "recommended_actions",
                    "confidence",
                    "confidence_reason",
                ],
                fallback=fallback,
                context="run_compare_report",
            )
            if err:
                parsed = fallback
            parsed["status"] = "ready"
            parsed["fallback_used"] = bool(err)
            parsed["ai_changes"] = ai_changes_of(compare_payload)
            parsed["markdown_report"] = _markdown_from_report(parsed)
            report = validate_agent_contract(
                RunCompareAgentOutput,
                parsed,
                agent_name="run_compare",
                agent_version="v1",
                fallback_used=bool(parsed.get("fallback_used")),
                confidence=int(parsed.get("confidence") or 0),
                evidence_refs=[{"type": "risk_level", "id": str(parsed.get("risk_level"))}],
                decision_reason=str(parsed.get("confidence_reason") or "run_compare_ai_report"),
            )
            report["ai_changes"] = ai_changes_of(compare_payload)
            return report
        except Exception as exc:
            # structlog's BoundLogger doesn't accept positional ``%s``-style
            # args; passing them raises TypeError mid-except, which would
            # propagate the original LLM failure as a 500 from the compare
            # endpoint. Use kwargs.
            logger.warning("run_compare_ai_report_fallback_used", error=str(exc))
            return build_validated_fallback_report(compare_payload)


def build_validated_fallback_report(
    compare_payload: dict[str, Any],
) -> dict[str, Any]:
    """Build the contract-valid deterministic report without creating an LLM."""
    fallback = build_fallback_report(compare_payload)
    fallback["status"] = "ready"
    fallback["fallback_used"] = True
    fallback["ai_changes"] = ai_changes_of(compare_payload)
    fallback["markdown_report"] = _markdown_from_report(fallback)
    report = validate_agent_contract(
        RunCompareAgentOutput,
        fallback,
        agent_name="run_compare",
        agent_version="v1",
        fallback_used=True,
        confidence=int(fallback.get("confidence") or 0),
        evidence_refs=[
            {"type": "risk_level", "id": str(fallback.get("risk_level"))}
        ],
        decision_reason="run_compare_deterministic_fallback",
    )
    report["ai_changes"] = ai_changes_of(compare_payload)
    return report


def _available_ai_diff(compare_payload: dict[str, Any]) -> dict[str, Any] | None:
    brief = compare_payload.get("ai_analysis_diff")
    if isinstance(brief, dict) and brief.get("available"):
        return brief
    return None


def ai_changes_of(compare_payload: dict[str, Any]) -> list[str]:
    """The report's ``ai_changes``: the deterministic lines of the payload's
    ``ai_analysis_diff`` (``ai_history_core.build_ai_changes``), or ``[]``. Never
    model output, so the list is the same whether the LLM answered or not."""
    brief = _available_ai_diff(compare_payload)
    changes = brief.get("ai_changes") if brief else None
    return [str(item) for item in changes] if isinstance(changes, list) else []


def _top_deltas(compare_payload: dict[str, Any], classification: str, limit: int = 8) -> list[dict[str, Any]]:
    return [
        {
            "test_name": d.get("test_name"),
            "suite_name": d.get("suite_name"),
            "left_status": d.get("left_status"),
            "right_status": d.get("right_status"),
            "delta_duration_ms": d.get("delta_duration_ms"),
            "error_hint": d.get("error_message"),
        }
        for d in compare_payload.get("test_deltas", [])
        if d.get("classification") == classification
    ][:limit]


def _build_context(compare_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope": compare_payload.get("scope"),
        "suite_name": compare_payload.get("suite_name"),
        "selection": compare_payload.get("selection"),
        "left": compare_payload.get("left"),
        "right": compare_payload.get("right"),
        "deltas": {
            "delta_total": compare_payload.get("delta_total"),
            "delta_passed": compare_payload.get("delta_passed"),
            "delta_failed": compare_payload.get("delta_failed"),
            "delta_broken": compare_payload.get("delta_broken"),
            "delta_skipped": compare_payload.get("delta_skipped"),
            "delta_pass_rate": compare_payload.get("delta_pass_rate"),
            "delta_duration_ms": compare_payload.get("delta_duration_ms"),
            "new_failures": compare_payload.get("new_failures"),
            "fixed": compare_payload.get("fixed"),
            "still_failing": compare_payload.get("still_failing"),
            "regressed": compare_payload.get("regressed"),
            "duration_spikes": compare_payload.get("duration_spikes"),
            "new_tests": compare_payload.get("new_tests"),
            "removed_tests": compare_payload.get("removed_tests"),
            "renamed": compare_payload.get("renamed"),
        },
        "top_new_failures": _top_deltas(compare_payload, "new_failure"),
        "top_regressions": _top_deltas(compare_payload, "regressed"),
        "top_still_failing": _top_deltas(compare_payload, "still_failing"),
        "top_duration_spikes": _top_deltas(compare_payload, "duration_spike"),
        "top_fixed": _top_deltas(compare_payload, "fixed"),
        **_ai_analysis_context(compare_payload),
    }


def _ai_analysis_context(compare_payload: dict[str, Any]) -> dict[str, Any]:
    """AIH-03: how the AI analysis changed between the two runs, as plain data for the
    model (only when it could be computed; at most ``_CONTEXT_TEST_CHANGES`` test changes)."""
    brief = _available_ai_diff(compare_payload)
    if brief is None:
        return {}
    changes = dict(brief)
    test_changes = changes.get("test_changes")
    if isinstance(test_changes, list):
        changes["test_changes"] = test_changes[:_CONTEXT_TEST_CHANGES]
    return {"ai_analysis_changes": changes}


def build_fallback_report(compare_payload: dict[str, Any]) -> dict[str, Any]:
    new_failures = int(compare_payload.get("new_failures") or 0)
    regressed = int(compare_payload.get("regressed") or 0)
    still_failing = int(compare_payload.get("still_failing") or 0)
    duration_spikes = int(compare_payload.get("duration_spikes") or 0)
    fixed = int(compare_payload.get("fixed") or 0)
    delta_pass_rate = compare_payload.get("delta_pass_rate")
    suite = compare_payload.get("suite_name")
    scope_label = f"suite {suite}" if suite else "the selected runs"

    risk_level = "LOW"
    if new_failures >= 5 or regressed >= 5:
        risk_level = "CRITICAL"
    elif new_failures or regressed:
        risk_level = "HIGH"
    elif still_failing or duration_spikes:
        risk_level = "MEDIUM"

    key_differences = [
        f"{new_failures} new failure(s)",
        f"{regressed} regression(s)",
        f"{fixed} fixed test(s)",
        f"{duration_spikes} duration spike(s)",
    ]
    if delta_pass_rate is not None:
        key_differences.insert(0, f"Pass rate changed by {delta_pass_rate:+.1f} percentage points")

    executive_summary = (
        f"Comparison for {scope_label} found {new_failures} new failure(s), "
        f"{regressed} regression(s), and {fixed} fixed test(s). "
        f"Risk level is {risk_level} based on deterministic status and duration deltas. "
        "Insufficient AI analysis evidence for root-cause claims."
    )

    return {
        "status": "ready",
        "executive_summary": executive_summary,
        "risk_level": risk_level,
        "key_differences": key_differences,
        "new_risks": [
            item.get("test_name") or "Unnamed test"
            for item in _top_deltas(compare_payload, "new_failure", limit=5)
        ],
        "resolved_risks": [
            item.get("test_name") or "Unnamed test"
            for item in _top_deltas(compare_payload, "fixed", limit=5)
        ],
        "duration_concerns": [
            item.get("test_name") or "Unnamed test"
            for item in _top_deltas(compare_payload, "duration_spike", limit=5)
        ],
        "recommended_actions": _recommended_actions(risk_level, new_failures, regressed, duration_spikes),
        "confidence": 65 if risk_level in ("LOW", "MEDIUM") else 75,
        "confidence_reason": "Deterministic comparison was available; root-cause evidence was not included.",
        "fallback_used": True,
    }


def _recommended_actions(
    risk_level: str,
    new_failures: int,
    regressed: int,
    duration_spikes: int,
) -> list[str]:
    actions: list[str] = []
    if new_failures or regressed:
        actions.append("Triage new failures before release decisions.")
    if duration_spikes:
        actions.append("Review duration spikes for infrastructure or performance regressions.")
    if risk_level in ("LOW", "MEDIUM"):
        actions.append("Review the diff table and rerun only the impacted suite if needed.")
    return actions or ["No immediate action required beyond normal review."]


def _markdown_from_report(report: dict[str, Any]) -> str:
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) or "- None"

    sections = [
        "## Executive Summary\n" + str(report.get("executive_summary") or ""),
        "## Key Differences\n" + bullets(report.get("key_differences") or []),
        "## Recommended Actions\n" + bullets(report.get("recommended_actions") or []),
        f"## Confidence\n{report.get('confidence', 0)}/100 - {report.get('confidence_reason', '')}",
    ]
    ai_changes = report.get("ai_changes") or []
    if ai_changes:
        sections.append("## AI analysis changes\n" + bullets([str(item) for item in ai_changes]))
    return "\n\n".join(sections)
