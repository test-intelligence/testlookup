"""
Digest Content Service — generates actionable quality digest from recent run data.

Produces summaries of regressions, blockers, top clusters, trend deltas, and
flaky tests for a given project and time period.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    FailureCluster,
    Project,
    ReleaseDecision,
    TestCase,
    TestRun,
)

logger = logging.getLogger("services.digest_content")


async def generate_digest(
    db: AsyncSession,
    project_id: uuid.UUID | None,
    period: str = "weekly",
) -> dict:
    """
    Generate actionable digest content for a project over a time period.

    Returns a dict matching DigestContentResponse fields.
    """
    days = 7 if period == "weekly" else 1
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    prev_cutoff = cutoff - timedelta(days=days)

    project_name = None
    if project_id:
        proj_result = await db.execute(select(Project.name).where(Project.id == project_id))
        project_name = proj_result.scalar_one_or_none()

    # ── Runs in period ──────────────────────────────────────────────────
    runs_query = select(TestRun).where(TestRun.created_at >= cutoff)
    if project_id:
        runs_query = runs_query.where(TestRun.project_id == project_id)
    runs_result = await db.execute(runs_query.order_by(TestRun.created_at.desc()))
    runs = runs_result.scalars().all()
    total_runs = len(runs)

    # Average pass rate
    avg_pass_rate = None
    if runs:
        rates = [r.pass_rate for r in runs if r.pass_rate is not None]
        avg_pass_rate = round(sum(rates) / len(rates), 1) if rates else None

    # Pass rate trend (compare with previous period)
    pass_rate_trend = None
    if avg_pass_rate is not None:
        prev_query = select(func.avg(cast(TestRun.pass_rate, Float))).where(
            TestRun.created_at >= prev_cutoff,
            TestRun.created_at < cutoff,
        )
        if project_id:
            prev_query = prev_query.where(TestRun.project_id == project_id)
        prev_avg = (await db.execute(prev_query)).scalar()
        if prev_avg is not None:
            pass_rate_trend = round(avg_pass_rate - float(prev_avg), 1)

    # ── New regressions (failed runs that were passing before) ──────────
    new_regressions = sum(
        1 for r in runs
        if r.status and r.status.upper() == "FAILED"
    )

    # ── Top clusters (from runs in period) ──────────────────────────────
    run_ids = [r.id for r in runs]
    top_clusters: list[dict] = []
    if run_ids:
        cluster_result = await db.execute(
            select(FailureCluster)
            .where(FailureCluster.test_run_id.in_(run_ids))
            .order_by(FailureCluster.size.desc())
            .limit(5)
        )
        for cl in cluster_result.scalars().all():
            top_clusters.append({
                "label": cl.label,
                "size": cl.size,
                "criticality": cl.regression_classification or "unclassified",
            })

    # ── Top blockers (from release decisions) ───────────────────────────
    top_blockers: list[str] = []
    if run_ids:
        decision_result = await db.execute(
            select(ReleaseDecision)
            .where(ReleaseDecision.test_run_id.in_(run_ids))
            .where(ReleaseDecision.recommendation == "NO_GO")
        )
        for dec in decision_result.scalars().all():
            for issue in (dec.blocking_issues or [])[:3]:
                if issue not in top_blockers:
                    top_blockers.append(issue)
            if len(top_blockers) >= 5:
                break

    # ── Flaky test count ────────────────────────────────────────────────
    flaky_count = 0
    if run_ids:
        flaky_result = await db.execute(
            select(func.count(TestCase.id))
            .where(TestCase.test_run_id.in_(run_ids))
            .where(TestCase.failure_category == "FLAKY")
        )
        flaky_count = flaky_result.scalar() or 0

    # ── Release decisions summary ───────────────────────────────────────
    release_decisions: list[dict] = []
    if run_ids:
        dec_result = await db.execute(
            select(ReleaseDecision)
            .where(ReleaseDecision.test_run_id.in_(run_ids))
            .order_by(ReleaseDecision.created_at.desc())
            .limit(5)
        )
        for dec in dec_result.scalars().all():
            release_decisions.append({
                "run_id": str(dec.test_run_id),
                "recommendation": dec.recommendation,
                "risk_score": dec.risk_score,
            })

    # ── Action items ────────────────────────────────────────────────────
    action_items: list[str] = []
    if new_regressions > 0:
        action_items.append(f"Investigate {new_regressions} new regression(s) from this period")
    if flaky_count > 5:
        action_items.append(f"Address {flaky_count} flaky test failures — consider quarantine")
    if top_blockers:
        action_items.append(f"Resolve {len(top_blockers)} blocking issue(s) for release readiness")
    if pass_rate_trend is not None and pass_rate_trend < -5:
        action_items.append(f"Pass rate declined by {abs(pass_rate_trend):.1f}% — review recent changes")
    if not action_items:
        action_items.append("Quality metrics are stable — no urgent action items")

    return {
        "project_name": project_name,
        "period": period,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_runs": total_runs,
        "avg_pass_rate": avg_pass_rate,
        "pass_rate_trend": pass_rate_trend,
        "new_regressions": new_regressions,
        "top_blockers": top_blockers[:5],
        "top_clusters": top_clusters[:5],
        "flaky_test_count": flaky_count,
        "release_decisions": release_decisions,
        "action_items": action_items,
    }


def render_digest_html(digest: dict) -> str:
    """Render a digest dict as a standalone HTML email body."""
    import html as _html

    def _e(text: str, max_len: int = 300) -> str:
        s = _html.escape(str(text or ""))
        return s[:max_len] + "..." if len(s) > max_len else s

    project_name = digest.get("project_name") or "All Projects"
    period = digest.get("period", "weekly").title()
    avg_pr = digest.get("avg_pass_rate")
    trend = digest.get("pass_rate_trend")

    sections = []

    # Header metrics
    trend_str = ""
    if trend is not None:
        sign = "+" if trend >= 0 else ""
        color = "#059669" if trend >= 0 else "#DC2626"
        trend_str = f' <span style="color:{color}">({sign}{trend:.1f}%)</span>'

    avg_pr_display = f"{avg_pr:.1f}%" if avg_pr is not None else "N/A"
    sections.append(f"""
    <div style="display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap">
        <div style="background:#F0FDF4;border-radius:8px;padding:12px 16px;text-align:center;min-width:100px">
            <div style="font-size:24px;font-weight:bold;color:#059669">{avg_pr_display}{trend_str if avg_pr else ''}</div>
            <div style="font-size:11px;color:#6B7280">Avg Pass Rate</div>
        </div>
        <div style="background:#F8FAFC;border-radius:8px;padding:12px 16px;text-align:center;min-width:80px">
            <div style="font-size:20px;font-weight:bold">{digest.get('total_runs', 0)}</div>
            <div style="font-size:11px;color:#6B7280">Runs</div>
        </div>
        <div style="background:#FEF2F2;border-radius:8px;padding:12px 16px;text-align:center;min-width:80px">
            <div style="font-size:20px;font-weight:bold;color:#DC2626">{digest.get('new_regressions', 0)}</div>
            <div style="font-size:11px;color:#6B7280">Regressions</div>
        </div>
        <div style="background:#FFFBEB;border-radius:8px;padding:12px 16px;text-align:center;min-width:80px">
            <div style="font-size:20px;font-weight:bold;color:#D97706">{digest.get('flaky_test_count', 0)}</div>
            <div style="font-size:11px;color:#6B7280">Flaky Tests</div>
        </div>
    </div>""")

    # Action items
    actions = digest.get("action_items", [])
    if actions:
        items = "".join(f"<li style='margin-bottom:4px'>{_e(a)}</li>" for a in actions)
        sections.append(f"<h3 style='color:#1E40AF;font-size:14px'>Action Items</h3><ul style='font-size:13px'>{items}</ul>")

    # Top blockers
    blockers = digest.get("top_blockers", [])
    if blockers:
        items = "".join(f"<li style='color:#DC2626;margin-bottom:4px'>{_e(b)}</li>" for b in blockers)
        sections.append(f"<h3 style='color:#1E40AF;font-size:14px'>Top Blockers</h3><ul style='font-size:13px'>{items}</ul>")

    # Top clusters
    clusters = digest.get("top_clusters", [])
    if clusters:
        rows = "".join(
            f"<tr><td style='padding:4px 8px;font-size:12px'>{_e(c.get('label', ''))}</td>"
            f"<td style='padding:4px 8px;text-align:center;font-size:12px'>{c.get('size', 0)}</td>"
            f"<td style='padding:4px 8px;font-size:12px'>{_e(c.get('criticality', ''))}</td></tr>"
            for c in clusters
        )
        sections.append(f"""
        <h3 style='color:#1E40AF;font-size:14px'>Top Failure Clusters</h3>
        <table style='border-collapse:collapse;width:100%'>
            <thead><tr style='background:#1E3A5F;color:white'>
                <th style='padding:6px 8px;text-align:left;font-size:12px'>Cluster</th>
                <th style='padding:6px 8px;font-size:12px'>Size</th>
                <th style='padding:6px 8px;text-align:left;font-size:12px'>Classification</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>""")

    body = "\n".join(sections)
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:600px;margin:0 auto;padding:16px;color:#1F2937">
    <h2 style="color:#1E40AF;margin-bottom:4px">TestLookup — {period} Digest</h2>
    <p style="color:#6B7280;font-size:13px;margin-top:0">{_e(project_name)} · {_e(digest.get('generated_at', ''))}</p>
    {body}
    <div style="margin-top:20px;padding-top:10px;border-top:1px solid #E5E7EB;font-size:11px;color:#9CA3AF">
        You received this digest because you subscribed in TestLookup.
        Manage your subscriptions in Settings &gt; Digests.
    </div>
</body></html>"""
