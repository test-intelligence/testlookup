"""
Report Composition Service — transforms RunIntelligenceSnapshot into structured report sections.

Reads the snapshot payload through ``intelligence_snapshot_service.get_or_compute``,
which refuses a stale or obsolete-schema row and recomputes instead — an export
must not carry a superseded verdict.
Supports two layouts: executive (summary-level) and engineering (full detail).
"""
from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import TestRun
from app.services.report_export_sanitizer import sanitize_report_export_payload

logger = logging.getLogger("services.report_composition")


# ── Type coercion ────────────────────────────────────────────────────────────
# A snapshot payload is free-form JSON and an imported archive's snapshot is
# used as stored, so a field the server always computes as a number can hold
# any text. The renderers print these fields as numbers: coerce them here, so a
# share page or a PDF never receives text where a number is expected.

def _number(value: Any) -> int | float | None:
    """``value`` as the number it is (or spells), else ``None``."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            pass
        try:
            parsed = float(text)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _as_float(value: Any, default: float = 0.0) -> float:
    number = _number(value)
    return default if number is None else float(number)


def _as_int(value: Any, default: int = 0) -> int:
    number = _number(value)
    return default if number is None else int(number)


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return value if isinstance(value, str) else str(value)


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _first_number(*candidates: Any) -> int | float | None:
    """The first candidate that is a non-zero number (the payload's value, then
    the run row's), matching the ``a or b or 0`` these fields used before."""
    for candidate in candidates:
        number = _number(candidate)
        if number:
            return number
    return None


def _with_numbers(items: Any, keys: tuple[str, ...], *, integer: bool = False) -> list[dict]:
    """``items`` as a list of dicts whose ``keys`` (when present) are numbers."""
    rows: list[dict] = []
    for item in _as_list(items):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        for key in keys:
            if key in row:
                number = _number(row[key])
                if number is None:
                    row[key] = 0
                else:
                    row[key] = int(number) if integer else number
        rows.append(row)
    return rows


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

    run_data = _as_dict(payload.get("run"))

    # Every numeric field is coerced (see "Type coercion" above): a payload
    # value that is not a number falls back to the run row, then to zero.
    report = ReportData(
        run_id=str(run_id),
        build_number=_as_text(run_data.get("build_number") or (run.build_number if run else "N/A"), "N/A"),
        branch=str(run_data.get("branch") or (run.branch if run else "N/A")),
        project_name=_as_text(run_data.get("project_name", "")),
        generated_at=datetime.now(timezone.utc).isoformat(),
        layout=layout,
        pass_rate=float(_first_number(run_data.get("pass_rate"), run.pass_rate if run else None) or 0.0),
        total_tests=int(_first_number(run_data.get("total_tests"), run.total_tests if run else None) or 0),
        passed_tests=int(_first_number(run_data.get("passed_tests"), run.passed_tests if run else None) or 0),
        failed_tests=int(_first_number(run_data.get("failed_tests"), run.failed_tests if run else None) or 0),
        skipped_tests=int(_first_number(run_data.get("skipped_tests"), run.skipped_tests if run else None) or 0),
    )

    # ── Extract structured summary ──────────────────────────────────────
    summary = _as_dict(payload.get("structured_summary"))
    report.executive_summary = _as_text(
        summary.get("executive_summary")
        or summary.get("layer1_executive", "")
        or "No executive summary available."
    )

    # ── Executive panel ──────────────────────────────────────────────────
    report.executive_panel = _as_dict(summary.get("executive_panel"))

    # ── Release decision ────────────────────────────────────────────────
    decision = _as_dict(payload.get("release_decision"))
    report.release_recommendation = _as_text(decision.get("recommendation", "N/A"), "N/A")
    report.risk_score = _as_int(decision.get("risk_score", 0))
    report.composite_risk = _as_float(decision.get("composite_risk", 0.0))
    report.blocking_issues = _as_list(decision.get("blocking_issues"))
    report.conditions_for_go = _as_list(decision.get("conditions_for_go"))
    report.release_reasoning = _as_text(decision.get("reasoning", ""))

    # ── Dimension scores ────────────────────────────────────────────────
    report.dimension_scores = _with_numbers(
        payload.get("dimension_scores"), ("score", "weight", "contribution"),
    )

    # ── Category breakdown ──────────────────────────────────────────────
    report.category_breakdown = {
        str(category): _as_int(count)
        for category, count in _as_dict(payload.get("category_breakdown")).items()
    }

    # ── Affected suites ─────────────────────────────────────────────────
    report.affected_suites = _as_list(payload.get("affected_suites"))

    # ── Action plan (from layer 4) ──────────────────────────────────────
    layer4 = _as_dict(summary.get("layer4_action_plan"))
    report.action_plan = {
        "immediate_mitigation": _as_list(layer4.get("immediate_mitigation")),
        "fix_recommendations": _as_list(layer4.get("fix_recommendations")),
        "validation_steps": _as_list(layer4.get("validation_steps")),
        "owner_hints": _as_dict(layer4.get("owner_hints")),
    }

    # ── Baseline diff ───────────────────────────────────────────────────
    diff = _as_dict(payload.get("what_changed_since_last_good_run"))
    new_failures = _as_list(diff.get("new_failures"))
    delta = _number(diff.get("pass_rate_delta"))
    report.baseline_diff = {
        "pass_rate_delta": None if delta is None else float(delta),
        "new_failures_count": len(new_failures),
        "resolved_count": len(_as_list(diff.get("resolved_failures"))),
        "regression_classification": diff.get("regression_classification", "unclassified"),
        "new_failures": new_failures[:10],
    }

    # ── Provenance ──────────────────────────────────────────────────────
    prov = _as_dict(payload.get("provenance"))
    report.provenance = {
        "confidence": _number(prov.get("confidence")),
        "sources_used": _as_list(prov.get("sources_used")),
        "evidence_count": _as_int(prov.get("evidence_count", 0)),
        "deterministic_checks": _as_list(prov.get("deterministic_checks_used")),
    }

    # ── Engineering-only sections ───────────────────────────────────────
    if layout == "engineering":
        report.failure_clusters = _with_numbers(payload.get("failure_clusters"), ("size",), integer=True)
        report.top_analyses = _with_numbers(payload.get("top_analyses"), ("confidence_score",))[:20]
        report.defect_candidates = _as_list(payload.get("defect_candidates"))
        # Restricted evidence is intentionally absent from downloadable reports.
        report.evidence_artifacts = []

    return report
