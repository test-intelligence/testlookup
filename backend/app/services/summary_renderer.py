"""
Summary Renderer — LLM narrative generator for audience-specific summaries.

Used by SummaryAgent for on-demand mode generation (developer / manager).
Each mode has a distinct prompt that emphasises different aspects of the context.
Deterministic fallback is produced when LLM is unavailable.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, cast

from app.services.summary_assembler import build_context_string, extract_citations

logger = logging.getLogger("services.summary_renderer")

_SYSTEM_PROMPT = """\
You are a QA Engineering Lead writing a structured post-run analysis report.
Be factual, direct, and actionable. Focus on failures and risks.
Do not pad the report. Base every statement strictly on the data provided.
"""

_DEVELOPER_PROMPT = """\
{system}

You are explaining a test run failure to the engineering team who will fix it.
Produce a developer-focused summary in JSON (no markdown fences):

{{
  "headline": "one-sentence summary of the dominant failure",
  "root_cause_analysis": "2-3 sentences on the likely root cause with technical detail",
  "evidence_highlights": ["key evidence item 1", "key evidence item 2"],
  "fix_recommendations": ["specific fix 1", "specific fix 2", "specific fix 3"],
  "validation_steps": ["how to verify the fix"],
  "similar_historical_context": "mention if similar failures occurred recently (or 'No similar historical failures found')"
}}

Data:
{context}"""

_MANAGER_PROMPT = """\
{system}

You are summarising a test run for an engineering manager or release manager.
Produce a manager-focused summary in JSON (no markdown fences):

{{
  "executive_summary": "2-3 sentences: business impact, release signal, next action",
  "release_recommendation": "GO | CONDITIONAL_GO | NO_GO — and why in one sentence",
  "scope_of_impact": "which features/services are affected",
  "key_risks": ["top risk 1", "top risk 2"],
  "recommended_decisions": ["decision needed from manager 1", "decision needed 2"],
  "timeline_guidance": "urgency — e.g. 'Fix required before this sprint release'"
}}

Data:
{context}"""


async def render_developer_summary(
    assembled: dict[str, Any],
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    """Generate or fall back to a developer-mode summary."""
    context = build_context_string(assembled, mode="developer")

    if not fallback_reason:
        try:
            from app.services.llm_factory import get_llm

            llm = get_llm()
            resp = await llm.ainvoke(
                _DEVELOPER_PROMPT.format(system=_SYSTEM_PROMPT, context=context)
            )
            raw = str(resp.content) if hasattr(resp, "content") else str(resp)
            raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
            raw = re.sub(r"\s*```$", "", raw.strip(), flags=re.MULTILINE)
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                parsed = cast(dict[str, Any], json.loads(match.group()))
                citations = extract_citations(
                    json.dumps(parsed),
                    assembled.get("evidence_snippets", []),
                )
                return {**parsed, "citations": citations, "fallback_used": False}
        except Exception as exc:
            logger.warning("Developer summary LLM call failed: %s", exc)
            fallback_reason = str(exc)

    return _fallback_developer(assembled, fallback_reason)


async def render_manager_summary(
    assembled: dict[str, Any],
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    """Generate or fall back to a manager-mode summary."""
    context = build_context_string(assembled, mode="manager")

    if not fallback_reason:
        try:
            from app.services.llm_factory import get_llm

            llm = get_llm()
            resp = await llm.ainvoke(
                _MANAGER_PROMPT.format(system=_SYSTEM_PROMPT, context=context)
            )
            raw = str(resp.content) if hasattr(resp, "content") else str(resp)
            raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
            raw = re.sub(r"\s*```$", "", raw.strip(), flags=re.MULTILINE)
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                parsed = cast(dict[str, Any], json.loads(match.group()))
                citations = extract_citations(
                    json.dumps(parsed),
                    assembled.get("evidence_snippets", []),
                )
                return {**parsed, "citations": citations, "fallback_used": False}
        except Exception as exc:
            logger.warning("Manager summary LLM call failed: %s", exc)
            fallback_reason = str(exc)

    return _fallback_manager(assembled, fallback_reason)


def _fallback_developer(
    assembled: dict[str, Any], reason: str | None
) -> dict[str, Any]:
    facts = assembled.get("facts", {})
    top = assembled.get("top_analyses", [])
    top_cause = (
        top[0].get("root_cause_summary", "Unknown root cause")
        if top
        else "No high-confidence root causes found"
    )
    actions: list[str] = []
    for a in top[:3]:
        actions.extend(a.get("recommended_actions", [])[:2])
    return {
        "headline": (
            f"Build {facts.get('build')} has "
            f"{facts.get('failed_tests', 0)} failures "
            f"({facts.get('pass_rate', 0):.1f}% pass rate)"
        ),
        "root_cause_analysis": top_cause,
        "evidence_highlights": [
            ev.get("excerpt", "")[:100]
            for ev in assembled.get("evidence_snippets", [])[:2]
        ],
        "fix_recommendations": (
            actions[:3] or ["Review failing tests and investigate root cause"]
        ),
        "validation_steps": ["Re-run failing tests after applying fixes"],
        "similar_historical_context": _format_similar(
            assembled.get("similar_failures", [])
        ),
        "citations": [],
        "fallback_used": True,
        "fallback_reason": reason,
    }


def _fallback_manager(
    assembled: dict[str, Any], reason: str | None
) -> dict[str, Any]:
    facts = assembled.get("facts", {})
    ri = assembled.get("release_inputs", {})
    rec = ri.get("recommendation", "CONDITIONAL_GO")
    pass_rate = float(facts.get("pass_rate", 0))
    return {
        "executive_summary": (
            f"Build {facts.get('build')} completed with "
            f"{facts.get('failed_tests', 0)} failures and "
            f"{pass_rate:.1f}% pass rate. "
            f"The dominant failure type is "
            f"{str(facts.get('top_category', 'unknown')).replace('_', ' ').lower()}. "
            f"Release recommendation: {rec.replace('_', ' ')}."
        ),
        "release_recommendation": (
            f"{rec} — based on {pass_rate:.1f}% pass rate and failure analysis"
        ),
        "scope_of_impact": (
            f"{facts.get('cluster_count', 0)} failure clusters "
            f"across {facts.get('failed_tests', 0)} tests"
        ),
        "key_risks": (
            ri.get("blocking_issues", [])[:2]
            or ["Review failing tests before release"]
        ),
        "recommended_decisions": (
            ri.get("conditions_for_go", [])[:2]
            or ["Assess release readiness manually"]
        ),
        "timeline_guidance": "Review before next scheduled release",
        "citations": [],
        "fallback_used": True,
        "fallback_reason": reason,
    }


def _format_similar(similar: list[dict]) -> str:
    if not similar:
        return "No similar historical failures found"
    names = [s.get("test_name", "") for s in similar[:3] if s.get("test_name")]
    return (
        f"Similar failures found: {', '.join(names)}"
        if names
        else "Similar failures found in recent runs"
    )
