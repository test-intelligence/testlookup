"""
Summary Assembler — deterministic context builder for the summary agent.

Collects structured facts, evidence snippets, cluster rankings, release inputs,
and owner hints from PostgreSQL + pipeline state. No LLM calls.

The assembler output is consumed by:
  - SummaryAgent._generate_structured_report() (LLM narrative layer)
  - SummaryAgent._build_fallback_structured_report() (deterministic fallback)
  - get_run_mode_summary() (on-demand mode generation)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("services.summary_assembler")


def assemble_context(
    run_data: dict[str, Any],
    anomalies: list[dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    clusters: Optional[list[dict[str, Any]]] = None,
    release_decision: Optional[dict[str, Any]] = None,
    similar_failures: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """
    Build a structured context dict from pipeline state.

    Returns:
        {
          facts: dict,              # run-level numeric facts
          top_analyses: list,       # high-confidence analysis bullets
          evidence_snippets: list,  # evidence excerpts
          cluster_rankings: list,   # clusters ordered by size/severity
          release_inputs: dict,     # release risk context
          owner_hints: dict,        # per-role action hints
          similar_failures: list,   # historically similar failures
          data_sources_used: list,  # unique evidence sources
        }
    """
    total = int(run_data.get("total_tests") or 0)
    failed = int(run_data.get("failed_tests") or 0)
    pass_rate = float(run_data.get("pass_rate") or 0.0)
    build = run_data.get("build_number") or "unknown"
    branch = run_data.get("branch") or "unknown"

    # Category counts
    category_counts: dict[str, int] = {}
    flaky_ids: list[str] = []
    data_sources: set[str] = set()

    for test_id, analysis in analyses.items():
        cat = _str_val(analysis.get("failure_category")) or "UNKNOWN"
        category_counts[cat] = category_counts.get(cat, 0) + 1
        if analysis.get("is_flaky"):
            flaky_ids.append(str(test_id))

    top_category = (
        max(category_counts, key=category_counts.__getitem__)
        if category_counts
        else "UNKNOWN"
    )

    # Top analyses (confidence >= 50)
    top_analyses: list[dict] = []
    for test_id, analysis in list(analyses.items())[:20]:
        conf = int(analysis.get("confidence_score") or 0)
        if conf >= 50:
            top_analyses.append({
                "test_id": str(test_id),
                "failure_category": _str_val(analysis.get("failure_category")) or "UNKNOWN",
                "confidence_score": conf,
                "root_cause_summary": str(analysis.get("root_cause_summary") or "")[:300],
                "is_flaky": bool(analysis.get("is_flaky")),
                "recommended_actions": list(analysis.get("recommended_actions") or [])[:3],
            })

    # Evidence snippets
    evidence_snippets: list[dict] = []
    for test_id, analysis in list(analyses.items())[:10]:
        for ev in (analysis.get("evidence_references") or [])[:2]:
            src = str(ev.get("source") or "")
            excerpt = str(ev.get("excerpt") or "")[:400]
            if src:
                data_sources.add(src)
            if excerpt:
                evidence_snippets.append({
                    "source": src,
                    "excerpt": excerpt,
                    "test_id": str(test_id),
                })

    # Cluster rankings
    cluster_rankings: list[dict] = []
    for c in (clusters or [])[:5]:
        cluster_rankings.append({
            "cluster_id": c.get("cluster_id", ""),
            "label": c.get("label", ""),
            "size": c.get("size", 0),
            "failure_category": c.get("failure_category", "UNKNOWN"),
            "severity_hint": c.get("severity_hint", ""),
        })

    # Release inputs
    release_inputs: dict[str, Any] = {}
    if release_decision:
        release_inputs = {
            "recommendation": release_decision.get("recommendation", ""),
            "risk_score": release_decision.get("risk_score", 0),
            "blocking_issues": release_decision.get("blocking_issues", []),
            "conditions_for_go": release_decision.get("conditions_for_go", []),
        }

    # Owner hints (deterministic based on top category)
    owner_hints = _build_owner_hints(top_category, pass_rate, release_inputs)

    # Anomaly log
    anomaly_bullets = [
        str(a.get("summary") or a.get("message") or a)[:200]
        for a in anomalies[:5]
    ]

    return {
        "facts": {
            "build": build,
            "branch": branch,
            "total_tests": total,
            "failed_tests": failed,
            "pass_rate": pass_rate,
            "top_category": top_category,
            "category_counts": category_counts,
            "flaky_count": len(flaky_ids),
            "flaky_test_ids": flaky_ids[:10],
            "anomaly_count": len(anomalies),
            "anomaly_bullets": anomaly_bullets,
            "cluster_count": len(clusters or []),
        },
        "top_analyses": top_analyses[:15],
        "evidence_snippets": evidence_snippets[:10],
        "cluster_rankings": cluster_rankings,
        "release_inputs": release_inputs,
        "owner_hints": owner_hints,
        "similar_failures": similar_failures or [],
        "data_sources_used": sorted(data_sources),
    }


def build_context_string(assembled: dict[str, Any], mode: str = "executive") -> str:
    """
    Convert assembled context into a text prompt string.
    mode affects verbosity: developer > manager > executive.
    """
    facts = assembled.get("facts", {})
    lines: list[str] = [
        f"Build: {facts.get('build')} | Branch: {facts.get('branch')}",
        (
            f"Results: {facts.get('total_tests')} tests, "
            f"{facts.get('failed_tests')} failures, "
            f"{facts.get('pass_rate', 0):.1f}% pass rate"
        ),
        f"Top failure category: {facts.get('top_category')}",
    ]

    # Release summary
    ri = assembled.get("release_inputs", {})
    if ri:
        lines.append(
            f"Release recommendation: {ri.get('recommendation', 'N/A')} "
            f"(risk score: {ri.get('risk_score', 0)})"
        )

    # Anomalies
    anomaly_bullets = facts.get("anomaly_bullets", [])
    if anomaly_bullets:
        lines.append(f"\nAnomalies ({facts.get('anomaly_count', 0)}):")
        lines.extend(f"  - {a}" for a in anomaly_bullets)

    # Cluster rankings
    clusters = assembled.get("cluster_rankings", [])
    if clusters:
        lines.append(f"\nTop failure clusters ({len(clusters)}):")
        for c in clusters:
            lines.append(
                f"  - [{c.get('size', 0)} tests] "
                f"{c.get('label', '')} ({c.get('failure_category', '')})"
            )

    # Top analyses (developer + manager see these)
    if mode in ("developer", "manager"):
        top = assembled.get("top_analyses", [])
        if top:
            lines.append(f"\nHigh-confidence root causes ({len(top)}):")
            for a in top[:10]:
                flaky_tag = " [FLAKY]" if a.get("is_flaky") else ""
                lines.append(
                    f"  - [{a.get('failure_category')}]{flaky_tag} "
                    f"conf={a.get('confidence_score')}%: "
                    f"{a.get('root_cause_summary')}"
                )

    # Evidence (developer only)
    if mode == "developer":
        evidence = assembled.get("evidence_snippets", [])
        if evidence:
            lines.append("\nEvidence excerpts:")
            for ev in evidence[:5]:
                lines.append(
                    f"  [{ev.get('source', '?')}] {ev.get('excerpt', '')[:150]}"
                )

    # Similar failures
    similar = assembled.get("similar_failures", [])
    if similar:
        lines.append(f"\nSimilar historical failures ({len(similar)}):")
        for sf in similar[:3]:
            lines.append(
                f"  - {sf.get('test_name', '')}: "
                f"{str(sf.get('error_message', ''))[:100]}"
            )

    return "\n".join(lines)


def extract_citations(
    text: str,
    evidence_snippets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Deterministically attach citations to text by matching evidence excerpts.
    Returns list of {source, excerpt, test_id} for evidence items whose
    key phrases appear verbatim or near-verbatim in the text.
    """
    citations: list[dict[str, Any]] = []
    text_lower = text.lower()
    for ev in evidence_snippets:
        excerpt = ev.get("excerpt", "")
        if not excerpt:
            continue
        # Match if first 40 chars of excerpt appear in text
        key_phrase = excerpt[:40].lower().strip()
        if len(key_phrase) >= 10 and key_phrase in text_lower:
            citations.append({
                "source": ev.get("source", ""),
                "excerpt": excerpt[:200],
                "test_id": ev.get("test_id", ""),
            })
    return citations


def _build_owner_hints(
    top_category: str,
    pass_rate: float,
    release_inputs: dict,
) -> dict:
    recommendation = release_inputs.get("recommendation", "")
    if top_category == "PRODUCT_BUG":
        dev_hint = "Investigate the identified product bugs and apply targeted code fixes."
        qa_hint = (
            "Re-run affected test suites after developer fix; add regression coverage."
        )
        sre_hint = (
            "Monitor error rates post-deploy; prepare rollback if error rate spikes."
        )
    elif top_category == "INFRASTRUCTURE":
        dev_hint = "Check service dependencies and environment configuration."
        qa_hint = (
            "Rerun tests in a stable environment; "
            "mark infrastructure failures as environmental blockers."
        )
        sre_hint = (
            "Investigate infra-level anomalies "
            "(pod crashes, resource limits, network issues)."
        )
    elif top_category in ("FLAKY", "AUTOMATION_DEFECT"):
        dev_hint = (
            "Review automation code for timing issues, "
            "shared state, or data dependencies."
        )
        qa_hint = (
            "Quarantine confirmed flaky tests; stabilize before next release cycle."
        )
        sre_hint = (
            "No immediate infra action required; monitor recurrence rate."
        )
    else:
        dev_hint = "Investigate root causes of unknown failures."
        qa_hint = "Triage failing tests and categorize failures before proceeding."
        sre_hint = (
            "Check infrastructure health metrics alongside test failure times."
        )

    rm_hint = (
        f"Release recommendation is {recommendation}."
        if recommendation
        else f"Pass rate is {pass_rate:.1f}% — assess release readiness manually."
    )

    return {
        "developer": dev_hint,
        "qa": qa_hint,
        "sre": sre_hint,
        "release_manager": rm_hint,
    }


def _str_val(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)
