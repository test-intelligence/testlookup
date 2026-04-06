"""Helpers for normalizing AI pipeline run summaries."""
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import AgentRunSummaryResponse
from app.models.postgres import AIAnalysis, ReleaseDecision, TestCase, TestRun


def _stringify_model_value(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def build_summary_markdown(doc: dict[str, Any]) -> str:
    sections: list[str] = []

    layer1 = doc.get("layer1_executive_summary") or doc.get("executive_summary") or ""
    layer2 = doc.get("layer2_incident_view") or {}
    layer3 = doc.get("layer3_evidence_pack") or {}
    layer4 = doc.get("layer4_action_plan") or {}

    if layer1:
        sections.append(f"## Executive Summary\n{layer1}")

    if layer2:
        sections.append(
            "## Incident View\n"
            f"**What failed:** {layer2.get('what_failed', '—')}\n"
            f"**Likely cause:** {layer2.get('likely_cause', '—')}\n"
            f"**Scope:** {layer2.get('scope', '—')}\n"
            f"**Criticality:** {layer2.get('criticality', '—')}\n"
            f"**Release impact:** {layer2.get('release_impact', '—')}"
        )

    if layer3:
        sources = ", ".join(layer3.get("data_sources_used", []))
        flaky = ", ".join(layer3.get("flaky_test_ids", []))
        anomalies = "\n".join(f"- {item}" for item in layer3.get("log_anomalies", []))
        sections.append(
            "## Evidence Pack\n"
            f"**Data sources:** {sources or '—'}\n"
            f"**Flaky tests:** {flaky or 'None'}\n"
            + (f"**Log anomalies:**\n{anomalies}" if anomalies else "")
        )

    if layer4:
        fix_recs = "\n".join(f"- {item}" for item in layer4.get("fix_recommendations", []))
        validation = "\n".join(f"- {item}" for item in layer4.get("validation_steps", []))
        sections.append(
            "## Action Plan\n"
            f"**Immediate mitigation:** {layer4.get('immediate_mitigation', '—')}\n\n"
            f"**Fix recommendations:**\n{fix_recs or '—'}\n\n"
            f"**Validation steps:**\n{validation or '—'}\n\n"
            f"**Rollback guidance:** {layer4.get('rollback_guidance', '—')}"
        )

    return "\n\n".join(section for section in sections if section).strip()


def normalize_summary_doc(run_id: str, doc: dict[str, Any]) -> AgentRunSummaryResponse:
    executive_summary = (doc.get("executive_summary") or doc.get("layer1_executive_summary") or "").strip()
    markdown_report = (doc.get("markdown_report") or "").strip()

    if not markdown_report:
        markdown_report = build_summary_markdown(doc)

    return AgentRunSummaryResponse(
        test_run_id=str(doc.get("test_run_id") or run_id),
        project_id=str(doc["project_id"]) if doc.get("project_id") is not None else None,
        build_number=str(doc["build_number"]) if doc.get("build_number") is not None else None,
        executive_summary=executive_summary,
        markdown_report=markdown_report,
        executive_panel=doc.get("executive_panel"),
        anomaly_count=int(doc.get("anomaly_count") or 0),
        is_regression=bool(doc.get("is_regression", False)),
        analysis_count=int(doc.get("analysis_count") or 0),
        generated_at=doc.get("generated_at"),
    )


async def build_fallback_summary(db: AsyncSession, run_id: str) -> AgentRunSummaryResponse | None:
    run = (
        await db.execute(select(TestRun).where(TestRun.id == run_id))
    ).scalar_one_or_none()
    if not run:
        return None

    analyses = (
        await db.execute(
            select(AIAnalysis)
            .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
            .where(TestCase.test_run_id == run.id)
            .limit(20)
        )
    ).scalars().all()

    release = (
        await db.execute(select(ReleaseDecision).where(ReleaseDecision.test_run_id == run.id))
    ).scalar_one_or_none()

    top_findings = [
        a.root_cause_summary.strip()
        for a in analyses
        if a.root_cause_summary and a.root_cause_summary.strip()
    ][:5]

    category_counts: dict[str, int] = {}
    for analysis in analyses:
        category = _stringify_model_value(analysis.failure_category, "UNKNOWN") or "UNKNOWN"
        category_counts[category] = category_counts.get(category, 0) + 1

    top_category = (
        max(category_counts, key=lambda category: category_counts[category])
        if category_counts
        else "UNKNOWN"
    )
    recommendation = release.recommendation if release else "PENDING"
    risk_score = release.risk_score if release else None

    executive_summary = (
        f"Build {run.build_number} completed with {run.failed_tests or 0} failing tests "
        f"at a {run.pass_rate or 0:.1f}% pass rate. "
        f"Top AI-classified failure category: {top_category.replace('_', ' ')}. "
        f"Release recommendation: {recommendation.replace('_', ' ')}"
        + (f" ({risk_score}/100)." if risk_score is not None else ".")
    )

    findings_md = "\n".join(f"- {item}" for item in top_findings) or "- No detailed AI findings were stored."
    blocking_md = "\n".join(f"- {item}" for item in (release.blocking_issues or [])) if release else ""
    markdown_report = "\n\n".join(
        section for section in [
            f"## Executive Summary\n{executive_summary}",
            "## Top AI Findings\n" + findings_md,
            (
                "## Release Decision\n"
                f"Recommendation: {recommendation.replace('_', ' ')}"
                + (f"\nRisk score: {risk_score}/100" if risk_score is not None else "")
                + (f"\n\nBlocking issues:\n{blocking_md}" if blocking_md else "")
            ),
        ] if section
    )

    # Build structured executive panel
    from app.services.executive_panel_builder import build_executive_panel

    executive_panel = build_executive_panel(
        run_data={
            "build_number": run.build_number,
            "branch": run.branch,
            "total_tests": run.total_tests or 0,
            "passed_tests": run.passed_tests or 0,
            "failed_tests": run.failed_tests or 0,
            "skipped_tests": run.skipped_tests or 0,
            "pass_rate": run.pass_rate or 0.0,
        },
        category_counts=category_counts,
        release_impact=_stringify_model_value(recommendation, "CONDITIONAL_GO"),
        risk_score=risk_score,
        recommended_actions=top_findings[:3],
    )

    return AgentRunSummaryResponse(
        test_run_id=str(run.id),
        project_id=str(run.project_id),
        build_number=run.build_number,
        executive_summary=executive_summary,
        markdown_report=markdown_report,
        executive_panel=executive_panel,
        anomaly_count=0,
        is_regression=False,
        analysis_count=len(analyses),
        generated_at=run.updated_at or run.created_at,
    )
