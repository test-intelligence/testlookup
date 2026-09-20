"""
Report Composition Service — transforms RunIntelligenceSnapshot into structured report sections.

Reads the snapshot payload through ``intelligence_snapshot_service.get_or_compute``,
which refuses a stale or obsolete-schema row and recomputes instead — an export
must not carry a superseded verdict.
Supports two layouts: executive (summary-level) and engineering (full detail).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import TestRun
from app.services.report_export_sanitizer import sanitize_report_export_payload

logger = logging.getLogger("services.report_composition")


@dataclass
class ReportSection:
    title: str
    content: Any


@dataclass
class ReportData:
    """Structured report data ready for rendering."""
    run_id: str
    build_number: str
    branch: str
    project_name: str
    generated_at: str
    layout: str  # "executive" | "engineering"
    pass_rate: float
    total_tests: int
    passed_tests: int
    failed_tests: int
    skipped_tests: int

    # Sections (populated based on layout)
    executive_summary: str = ""
    release_recommendation: str = ""
    risk_score: int = 0
    composite_risk: float = 0.0
    blocking_issues: list[str] = field(default_factory=list)
    conditions_for_go: list[str] = field(default_factory=list)
    release_reasoning: str = ""
    dimension_scores: list[dict] = field(default_factory=list)
    category_breakdown: dict[str, int] = field(default_factory=dict)
    affected_suites: list[dict] = field(default_factory=list)
    action_plan: dict = field(default_factory=dict)
    baseline_diff: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)

    # Structured executive panel (ES-2)
    executive_panel: dict = field(default_factory=dict)

    # Engineering-only sections
    failure_clusters: list[dict] = field(default_factory=list)
    top_analyses: list[dict] = field(default_factory=list)
    defect_candidates: list[dict] = field(default_factory=list)
    evidence_artifacts: list[dict] = field(default_factory=list)

    # E8.4: set when an unreviewed AI report is distributed under a project
    # opt-in or an explicit include_unreviewed; renderers print it first.
    draft_watermark: str = ""


async def compose_report(
    db: AsyncSession,
    run_id: uuid.UUID,
    layout: str = "executive",
) -> ReportData:
    """
    Read a CURRENT intelligence payload for run_id, return structured ReportData.

    Raises ValueError if no intelligence data is available.
    """
    # A CURRENT payload, never whatever happens to be in the table.
    #
    # This read used to be a bare select on run_id with no ``stale`` and no
    # ``schema_version`` predicate, so an exported or shared report served the
    # verdict as it stood before the override or defect promotion that
    # invalidated it — and would serve an obsolete-schema payload that
    # ``get_stale_snapshot`` refuses precisely because it has the wrong shape.
    # A share link is the most exposed channel in the product: anyone holding
    # the token reads it, with no login.
    from app.services.intelligence_snapshot_service import get_or_compute

    try:
        raw_payload = await get_or_compute(db, run_id)
    except Exception as exc:
        # Fail rather than fall back to the stale row: shipping a superseded
        # verdict to an external reader is the defect being fixed.
        logger.warning("Could not produce a current snapshot for run %s: %s", run_id, exc)
        raise ValueError(
            f"Intelligence for run {run_id} could not be refreshed for export. "
            "Retry, or re-run the investigation."
        ) from exc

    if not raw_payload:
        raise ValueError(f"No intelligence snapshot found for run {run_id}. Trigger deep investigation first.")

    payload = sanitize_report_export_payload(raw_payload)

    # Get test run info
    run_result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    run = run_result.scalar_one_or_none()

    run_data = payload.get("run", {})

    report = ReportData(
        run_id=str(run_id),
        build_number=run_data.get("build_number") or (run.build_number if run else "N/A"),
        branch=str(run_data.get("branch") or (run.branch if run else "N/A")),
        project_name=run_data.get("project_name", ""),
        generated_at=datetime.now(timezone.utc).isoformat(),
        layout=layout,
        pass_rate=run_data.get("pass_rate") or (run.pass_rate if run else 0.0) or 0.0,
        total_tests=run_data.get("total_tests") or (run.total_tests if run else 0) or 0,
        passed_tests=run_data.get("passed_tests") or (run.passed_tests if run else 0) or 0,
        failed_tests=run_data.get("failed_tests") or (run.failed_tests if run else 0) or 0,
        skipped_tests=run_data.get("skipped_tests") or (run.skipped_tests if run else 0) or 0,
    )

    # ── Extract structured summary ──────────────────────────────────────
    summary = payload.get("structured_summary") or {}
    report.executive_summary = (
        summary.get("executive_summary")
        or summary.get("layer1_executive", "")
        or "No executive summary available."
    )

    # ── Executive panel ──────────────────────────────────────────────────
    report.executive_panel = summary.get("executive_panel") or {}

    # ── Release decision ────────────────────────────────────────────────
    decision = payload.get("release_decision") or {}
    report.release_recommendation = decision.get("recommendation", "N/A")
    report.risk_score = decision.get("risk_score", 0)
    report.composite_risk = decision.get("composite_risk", 0.0) or 0.0
    report.blocking_issues = decision.get("blocking_issues", [])
    report.conditions_for_go = decision.get("conditions_for_go", [])
    report.release_reasoning = decision.get("reasoning", "")

    # ── Dimension scores ────────────────────────────────────────────────
    report.dimension_scores = payload.get("dimension_scores", [])

    # ── Category breakdown ──────────────────────────────────────────────
    report.category_breakdown = payload.get("category_breakdown", {})

    # ── Affected suites ─────────────────────────────────────────────────
    report.affected_suites = payload.get("affected_suites", [])

    # ── Action plan (from layer 4) ──────────────────────────────────────
    layer4 = summary.get("layer4_action_plan") or {}
    report.action_plan = {
        "immediate_mitigation": layer4.get("immediate_mitigation", []),
        "fix_recommendations": layer4.get("fix_recommendations", []),
        "validation_steps": layer4.get("validation_steps", []),
        "owner_hints": layer4.get("owner_hints", {}),
    }

    # ── Baseline diff ───────────────────────────────────────────────────
    diff = payload.get("what_changed_since_last_good_run") or {}
    report.baseline_diff = {
        "pass_rate_delta": diff.get("pass_rate_delta"),
        "new_failures_count": len(diff.get("new_failures", [])),
        "resolved_count": len(diff.get("resolved_failures", [])),
        "regression_classification": diff.get("regression_classification", "unclassified"),
        "new_failures": diff.get("new_failures", [])[:10],
    }

    # ── Provenance ──────────────────────────────────────────────────────
    prov = payload.get("provenance") or {}
    report.provenance = {
        "confidence": prov.get("confidence"),
        "sources_used": prov.get("sources_used", []),
        "evidence_count": prov.get("evidence_count", 0),
        "deterministic_checks": prov.get("deterministic_checks_used", []),
    }

    # ── Engineering-only sections ───────────────────────────────────────
    if layout == "engineering":
        report.failure_clusters = payload.get("failure_clusters", [])
        report.top_analyses = payload.get("top_analyses", [])[:20]
        report.defect_candidates = payload.get("defect_candidates", [])
        # Restricted evidence is intentionally absent from downloadable reports.
        report.evidence_artifacts = []

    return report
