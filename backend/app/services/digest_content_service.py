"""
Digest Content Service — generates actionable quality digest from recent run data.

Produces summaries of regressions, blockers, top clusters, trend deltas, and
flaky tests for a given project and time period.

PMF US-7.4 (delta digests): when the caller passes ``since`` (the previous
successful send for the subscription — ``DigestSubscription.last_delivered_at``
is the watermark), the digest is structured around WHAT CHANGED in the window
[since, now]: new failures (count + top 3), newly flaky, fixed/recovered,
quarantine debt, and gate-verdict changes. Absolute totals stay as a
secondary line. A window with no changes renders as a one-liner (or is
skipped entirely, per the subscription's ``send_when_unchanged``).

Agentic plan AI-7 (proactive narratives): the delta additionally carries
``investigations`` — completed Investigator verdicts in the window (count +
top ≤2 by confidence) — rendered as ONE clearly AI-labeled line; windows
with zero completed investigations add nothing (not even an empty line),
and the zero-change predicate ignores investigations entirely (they are
informational, never a "change"). Weekly project digests also fold in the
flaky-debt review drafts for teams WITHOUT a US-7.3 channel (teams WITH a
channel get theirs directly via ``flaky_debt_review``). All narrative text
is quoted from stored investigation verdicts — generating a digest makes
NO LLM calls (guarded by ``tests/test_proactive_narratives.py``).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    AgentInvestigation,
    FailureCluster,
    FlakyQuarantineRequest,
    FlakyQuarantineStatus,
    Project,
    ReleaseDecision,
    TestCase,
    TestRun,
    TestStatus,
)

logger = logging.getLogger("services.digest_content")

_FAILED_STATUSES = (TestStatus.FAILED.value, TestStatus.BROKEN.value)

# Quarantine states that count as "debt" — actively enforced entries.
_ACTIVE_QUARANTINE_STATUSES = (
    FlakyQuarantineStatus.QUARANTINED.value,
    FlakyQuarantineStatus.RECHECK_SCHEDULED.value,
    FlakyQuarantineStatus.RE_QUARANTINED.value,
)

# Human display names for the Investigator's fixed hypothesis ids (AI-7 —
# shared with the attached analysis report's investigations section).
CAUSE_DISPLAY: dict[str, str] = {
    "infra": "infrastructure",
    "commit": "code change",
    "environment": "environment drift",
    "known_flaky": "known-flaky",
    "regression": "regression",
    "unknown": "unknown",
}

# How many top verdicts the delta carries (AI-7 spec: ≤2).
_INVESTIGATION_TOP_N = 2


def is_zero_change(delta: dict) -> bool:
    """A delta window with no new failures, no newly flaky, no recoveries
    and no gate change is a zero-change window (quarantine debt is a
    standing stock, not a change — it does not block the one-liner).
    Completed investigations are informational and deliberately ignored."""
    return (
        int(delta.get("new_failures") or 0) == 0
        and int(delta.get("newly_flaky") or 0) == 0
        and int(delta.get("recovered") or 0) == 0
        and delta.get("gate_change") is None
    )


async def compute_digest_deltas(
    db: AsyncSession,
    project_id: uuid.UUID,
    since: datetime,
    now: datetime | None = None,
) -> dict:
    """Changes in the window [since, now] for one project.

    * new failures — fingerprints that FAILED/BROKEN in the window but not
      in the equal-length pre-window (i.e. they were not already failing at
      the watermark); count + top 3 by in-window failure count.
    * newly flaky — flaky-quarantine candidates detected in the window.
    * recovered — fingerprints that failed in the pre-window and have >= 1
      pass and zero fails in the window.
    * quarantine debt — standing stock: active entries, of which stale
      (past ``stale_at``) and ready-to-promote (US-5.4/5.5 lifecycle fields).
    * gate change — latest ReleaseDecision recommendation before the window
      vs. the latest inside it, when both exist and differ.
    """
    now = now or datetime.now(timezone.utc)
    window = now - since if now > since else timedelta(days=1)
    pre_start = since - window

    # ── Failed fingerprints in window (with names + counts) ─────────────
    window_failed_rows = (
        await db.execute(
            select(
                TestCase.test_fingerprint,
                func.max(TestCase.test_name).label("test_name"),
                func.count(TestCase.id).label("fail_count"),
            )
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(
                TestRun.project_id == project_id,
                TestCase.created_at >= since,
                TestCase.created_at < now,
                TestCase.status.in_(_FAILED_STATUSES),
                TestCase.test_fingerprint.isnot(None),
            )
            .group_by(TestCase.test_fingerprint)
        )
    ).all()
    window_failed = {
        row.test_fingerprint: (row.test_name, int(row.fail_count))
        for row in window_failed_rows
    }

    # ── Pre-window failed fingerprints (were already failing) ───────────
    pre_failed_rows = (
        await db.execute(
            select(TestCase.test_fingerprint)
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(
                TestRun.project_id == project_id,
                TestCase.created_at >= pre_start,
                TestCase.created_at < since,
                TestCase.status.in_(_FAILED_STATUSES),
                TestCase.test_fingerprint.isnot(None),
            )
            .distinct()
        )
    ).all()
    pre_failed = {row[0] for row in pre_failed_rows}

    new_failure_fps = [fp for fp in window_failed if fp not in pre_failed]
    new_failure_fps.sort(key=lambda fp: -window_failed[fp][1])
    new_failures_top = [window_failed[fp][0] for fp in new_failure_fps[:3]]

    # ── Recovered: failed pre-window, passing (and never failing) now ───
    recovered = 0
    if pre_failed:
        window_passed_rows = (
            await db.execute(
                select(TestCase.test_fingerprint)
                .join(TestRun, TestRun.id == TestCase.test_run_id)
                .where(
                    TestRun.project_id == project_id,
                    TestCase.created_at >= since,
                    TestCase.created_at < now,
                    TestCase.status == TestStatus.PASSED.value,
                    TestCase.test_fingerprint.in_(pre_failed),
                )
                .distinct()
            )
        ).all()
        recovered = sum(
            1 for row in window_passed_rows if row[0] not in window_failed
        )

    # ── Newly flaky: quarantine candidates detected in the window ───────
    newly_flaky = int(
        (
            await db.execute(
                select(func.count(FlakyQuarantineRequest.id)).where(
                    FlakyQuarantineRequest.project_id == project_id,
                    FlakyQuarantineRequest.detected_at >= since,
                    FlakyQuarantineRequest.detected_at < now,
                )
            )
        ).scalar()
        or 0
    )

    # ── Quarantine debt (US-5.4/US-5.5 lifecycle fields) ────────────────
    debt_row = (
        await db.execute(
            select(
                func.count(FlakyQuarantineRequest.id).label("active"),
                func.count(FlakyQuarantineRequest.id)
                .filter(FlakyQuarantineRequest.stale_at <= now)
                .label("stale"),
                func.count(FlakyQuarantineRequest.id)
                .filter(FlakyQuarantineRequest.ready_to_promote == True)  # noqa: E712
                .label("ready"),
            ).where(
                FlakyQuarantineRequest.project_id == project_id,
                FlakyQuarantineRequest.status.in_(_ACTIVE_QUARANTINE_STATUSES),
            )
        )
    ).one()
    quarantine_debt = {
        "active": int(debt_row.active or 0),
        "stale": int(debt_row.stale or 0),
        "ready_to_promote": int(debt_row.ready or 0),
    }

    # ── Gate verdict change (cheap: latest before vs latest inside) ─────
    async def _latest_recommendation(start, end) -> str | None:
        query = (
            select(ReleaseDecision.recommendation)
            .join(TestRun, TestRun.id == ReleaseDecision.test_run_id)
            .where(TestRun.project_id == project_id)
            .order_by(ReleaseDecision.created_at.desc())
            .limit(1)
        )
        if start is not None:
            query = query.where(ReleaseDecision.created_at >= start)
        if end is not None:
            query = query.where(ReleaseDecision.created_at < end)
        return (await db.execute(query)).scalar_one_or_none()

    gate_change = None
    gate_now = await _latest_recommendation(since, now)
    if gate_now is not None:
        gate_before = await _latest_recommendation(None, since)
        if gate_before is not None and gate_before != gate_now:
            gate_change = {"from": gate_before, "to": gate_now}

    # ── AI-7: completed Investigator verdicts in the window ─────────────
    # Read-only over the persisted agent_investigations rows (#383) — the
    # digest QUOTES stored verdicts, it never re-runs anything.
    inv_rows = (
        await db.execute(
            select(AgentInvestigation.verdict, TestRun.build_number)
            .join(TestRun, TestRun.id == AgentInvestigation.run_id)
            .where(
                AgentInvestigation.project_id == project_id,
                AgentInvestigation.status == "completed",
                AgentInvestigation.completed_at >= since,
                AgentInvestigation.completed_at < now,
            )
            .order_by(AgentInvestigation.completed_at.desc())
        )
    ).all()
    inv_top = sorted(
        (
            {
                "run_build": row.build_number,
                "primary_cause": (row.verdict or {}).get("primary_cause") or "unknown",
                "confidence": int((row.verdict or {}).get("confidence") or 0),
            }
            for row in inv_rows
        ),
        key=lambda item: -item["confidence"],
    )[:_INVESTIGATION_TOP_N]
    investigations = {"completed": len(inv_rows), "top": inv_top}

    return {
        "window_start": since.isoformat(),
        "window_end": now.isoformat(),
        "new_failures": len(new_failure_fps),
        "new_failures_top": new_failures_top,
        "newly_flaky": newly_flaky,
        "recovered": recovered,
        "quarantine_debt": quarantine_debt,
        "gate_change": gate_change,
        "investigations": investigations,
    }


async def generate_digest(
    db: AsyncSession,
    project_id: uuid.UUID | None,
    period: str = "weekly",
    since: datetime | None = None,
) -> dict:
    """
    Generate actionable digest content for a project over a time period.

    Returns a dict matching DigestContentResponse fields. When ``since``
    is provided (the previous digest send for the subscription), the
    window starts there and the result additionally carries ``delta`` /
    ``is_zero_change`` / ``changes_since`` (PMF US-7.4).
    """
    days = 7 if period == "weekly" else 1
    now = datetime.now(timezone.utc)
    cutoff = since if since is not None else now - timedelta(days=days)
    window = now - cutoff
    if window.total_seconds() <= 0:  # clock skew / same-instant watermark
        window = timedelta(days=days)
        cutoff = now - window
    prev_cutoff = cutoff - window

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

    digest = {
        "project_name": project_name,
        "period": period,
        "generated_at": now.isoformat(),
        "total_runs": total_runs,
        "avg_pass_rate": avg_pass_rate,
        "pass_rate_trend": pass_rate_trend,
        "new_regressions": new_regressions,
        "top_blockers": top_blockers[:5],
        "top_clusters": top_clusters[:5],
        "flaky_test_count": flaky_count,
        "release_decisions": release_decisions,
        "action_items": action_items,
        "latest_run_total_tests": runs[0].total_tests if runs else None,
    }

    # ── Delta structure (US-7.4) — only for watermark-driven digests ────
    if since is not None and project_id is not None:
        try:
            delta = await compute_digest_deltas(db, project_id, cutoff, now)
        except Exception as exc:  # noqa: BLE001 — a delta fault must not kill the digest
            logger.warning("Digest delta computation failed for %s: %s", project_id, exc)
            delta = None
        digest["changes_since"] = cutoff.isoformat()
        digest["delta"] = delta
        digest["is_zero_change"] = is_zero_change(delta) if delta is not None else False

    # ── AI-7: weekly flaky-debt review fold-in ──────────────────────────
    # Teams WITHOUT a US-7.3 channel get their draft as a digest section
    # (teams WITH a channel receive theirs directly via the Monday
    # ``dispatch_weekly_flaky_debt_reviews`` beat). Unconditional-when-
    # data-exists: an opt-in subscription flag would need a migration
    # (see the 0106/0107 precedents) and this slice ships without one.
    # A review fault must never kill the digest.
    if period == "weekly" and project_id is not None:
        try:
            from app.services.flaky_debt_review import build_flaky_debt_reviews
            from app.services.notification_routing import load_team_channels

            reviews = await build_flaky_debt_reviews(
                db, project_id, since=cutoff, now=now,
            )
            channels = await load_team_channels(db, project_id)
            folded = [
                team for team in reviews["teams"] if team["team"] not in channels
            ]
            if folded:
                digest["flaky_debt_review"] = {
                    "teams": folded,
                    "routed_team_count": len(reviews["teams"]) - len(folded),
                }
        except Exception as exc:  # noqa: BLE001 — review fault must not kill the digest
            logger.warning(
                "Flaky-debt review fold-in failed for %s: %s", project_id, exc
            )

    # ── US-12.2: engineer-hours-saved headline ──────────────────────────
    # Only set when the availability gate passes (enough history + a
    # nonzero leg in the last 30 days) — the renderers stay silent when
    # the key is absent, and the zero-change short-circuits return before
    # ever reaching it. A headline fault must never kill the digest.
    if project_id is not None:
        try:
            from app.services.value_metrics_service import compute_headline

            headline = await compute_headline(db, project_id)
            if headline.get("available"):
                digest["value_headline_hours_30d"] = round(
                    float(headline.get("hours_saved_30d") or 0.0), 1
                )
        except Exception as exc:  # noqa: BLE001 — headline fault must not kill the digest
            logger.warning(
                "Value headline computation failed for %s: %s", project_id, exc
            )

    return digest


def zero_change_line(digest: dict) -> str:
    """The one-line summary for a zero-change delta window (US-7.4)."""
    details = []
    tests = digest.get("latest_run_total_tests")
    if tests is not None:
        details.append(f"{int(tests)} tests")
    avg = digest.get("avg_pass_rate")
    if avg is not None:
        details.append(f"pass rate {avg:.1f}%")
    line = "No changes since the last digest"
    return f"{line} — {', '.join(details)}." if details else f"{line}."


def investigator_delta_line(delta: dict) -> str | None:
    """The ONE AI-labeled Investigator line for a delta window (AI-7).

    ``None`` when the window has no completed investigations — zero-
    investigation windows add nothing, not even an empty line. The text is
    assembled from the persisted verdicts only (no LLM calls here).
    """
    investigations = delta.get("investigations") or {}
    completed = int(investigations.get("completed") or 0)
    if completed <= 0:
        return None
    parts = []
    for item in (investigations.get("top") or [])[:_INVESTIGATION_TOP_N]:
        cause = CAUSE_DISPLAY.get(
            str(item.get("primary_cause")), str(item.get("primary_cause") or "unknown")
        )
        build = item.get("run_build")
        build_str = f"#{build}" if build else "a run"
        parts.append(f"{build_str} → {cause} ({int(item.get('confidence') or 0)}%)")
    tops = f" — {', '.join(parts)}" if parts else ""
    plural = "s" if completed != 1 else ""
    return (
        f"AI Investigator (shadow — informational): {completed} "
        f"investigation{plural} completed{tops} — evidence in the report"
    )


def _delta_lines(delta: dict) -> list[str]:
    """The delta dict as compact plain-text lines (shared by the Slack
    rendering and tests)."""
    lines: list[str] = []
    new_failures = int(delta.get("new_failures") or 0)
    top = delta.get("new_failures_top") or []
    top_str = f" (top: {', '.join(str(t) for t in top)})" if top else ""
    lines.append(
        f"New failures: {new_failures}{top_str} · "
        f"Newly flaky: {int(delta.get('newly_flaky') or 0)} · "
        f"Fixed: {int(delta.get('recovered') or 0)}"
    )
    debt = delta.get("quarantine_debt") or {}
    lines.append(
        f"Quarantine debt: {int(debt.get('active') or 0)} active "
        f"({int(debt.get('stale') or 0)} stale, "
        f"{int(debt.get('ready_to_promote') or 0)} ready to promote)"
    )
    gate = delta.get("gate_change")
    if gate:
        lines.append(f"Gate verdict changed: {gate.get('from')} → {gate.get('to')}")
    investigator_line = investigator_delta_line(delta)
    if investigator_line:
        lines.append(investigator_line)
    return lines


def render_digest_text(digest: dict) -> str:
    """Compact plain-text rendering for Slack/Teams digest delivery.

    Delta-first when a delta window is present; absolute totals stay as
    the secondary line. Zero-change windows collapse to the one-liner.
    """
    if digest.get("is_zero_change"):
        return zero_change_line(digest)

    lines: list[str] = []
    delta = digest.get("delta")
    if delta:
        lines.append("Since last digest:")
        lines.extend(_delta_lines(delta))
    totals = (
        f"Totals: {digest.get('total_runs', 0)} runs"
    )
    avg = digest.get("avg_pass_rate")
    if avg is not None:
        trend = digest.get("pass_rate_trend")
        trend_str = f" ({'+' if trend >= 0 else ''}{trend:.1f}%)" if trend is not None else ""
        totals += f" · pass rate {avg:.1f}%{trend_str}"
    totals += f" · {digest.get('flaky_test_count', 0)} flaky"
    lines.append(totals)
    # US-12.2: headline only present when the availability gate passed.
    hours_saved = digest.get("value_headline_hours_30d")
    if hours_saved is not None:
        lines.append(
            f"≈ {hours_saved:g} engineer-hours saved in the last 30 days "
            "(see /value-metrics)"
        )
    for item in (digest.get("action_items") or [])[:3]:
        lines.append(f"• {item}")
    # AI-7: weekly flaky-debt review drafts for teams without their own
    # channel (each team text carries its own automation label).
    for team in (digest.get("flaky_debt_review") or {}).get("teams") or []:
        if team.get("text"):
            lines.append("")
            lines.append(str(team["text"]))
    return "\n".join(lines)


# PMF US-7.5 — appended to Slack/Teams digest text when the subscription
# has the email report attachment enabled (content otherwise unchanged).
REPORT_ATTACHMENT_NOTE = "Full analysis report attached to the email digest"

# Apologetic note appended to the email digest body when the attachment
# build failed (the digest itself must still deliver — never-raises).
REPORT_BUILD_FAILED_NOTE = (
    "The attached analysis report could not be generated for this digest — "
    "sorry. The live report is available from the dashboard."
)


def digest_text_with_attachment_note(text: str, attachment_enabled: bool) -> str:
    """Append the one-line attachment pointer to a Slack/Teams digest
    rendering (US-7.5). Content is unchanged when the flag is off."""
    if not attachment_enabled:
        return text
    return f"{text}\n{REPORT_ATTACHMENT_NOTE}"


def append_digest_html_note(html: str, note: str) -> str:
    """Insert a small note just before ``</body>`` of a rendered digest
    email (used for the attachment-build-failure apology). Falls back to
    plain concatenation when the marker is missing."""
    import html as _html_mod

    snippet = (
        f'<p style="font-size:12px;color:#B45309;margin-top:12px">'
        f"{_html_mod.escape(note)}</p>"
    )
    if "</body>" in html:
        return html.replace("</body>", f"{snippet}</body>", 1)
    return html + snippet


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
    is_retro = digest.get("schedule_type") == "WEEKLY_RETRO"

    # US-7.4: a zero-change window renders as a single line, nothing else.
    if digest.get("is_zero_change"):
        return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:600px;margin:0 auto;padding:16px;color:#1F2937">
    <h2 style="color:#1E40AF;margin-bottom:4px">TestLookup — {period} Digest</h2>
    <p style="color:#6B7280;font-size:13px;margin-top:0">{_e(project_name)} · {_e(digest.get('generated_at', ''))}</p>
    <p style="font-size:14px;color:#1F2937">✅ {_e(zero_change_line(digest))}</p>
    <div style="margin-top:20px;padding-top:10px;border-top:1px solid #E5E7EB;font-size:11px;color:#9CA3AF">
        You received this digest because you subscribed in TestLookup.
        Manage your subscriptions in Settings &gt; Digests.
    </div>
</body></html>"""

    sections = []

    # US-7.4: delta-first block — what changed since the previous digest.
    delta = digest.get("delta")
    if delta:
        new_failures = int(delta.get("new_failures") or 0)
        top = delta.get("new_failures_top") or []
        top_html = ""
        if top:
            items = "".join(f"<li style='margin-bottom:2px'>{_e(t)}</li>" for t in top)
            top_html = f"<ul style='font-size:12px;margin:6px 0 0 0'>{items}</ul>"
        debt = delta.get("quarantine_debt") or {}
        gate = delta.get("gate_change")
        gate_html = (
            f"<div style='font-size:12px;color:#B45309;margin-top:6px'>Gate verdict changed: "
            f"<strong>{_e(gate.get('from'))}</strong> → <strong>{_e(gate.get('to'))}</strong></div>"
            if gate else ""
        )
        # AI-7: one Investigator line, only when the window had completed
        # investigations (zero-investigation windows add nothing).
        investigator_line = investigator_delta_line(delta)
        investigator_html = (
            f"<div style='font-size:12px;color:#6D28D9;margin-top:6px'>"
            f"{_e(investigator_line, 500)}</div>"
            if investigator_line else ""
        )
        sections.append(f"""
        <div style="background:#F8FAFC;border-left:4px solid #0EA5E9;padding:12px 16px;margin-bottom:16px;border-radius:4px">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:#0C4A6E;font-weight:600;margin-bottom:6px">Since last digest</div>
            <div style="display:flex;gap:16px;font-size:12px;color:#374151;flex-wrap:wrap">
                <span><strong style="color:#DC2626">{new_failures}</strong> new failure{'s' if new_failures != 1 else ''}</span>
                <span><strong style="color:#D97706">{int(delta.get('newly_flaky') or 0)}</strong> newly flaky</span>
                <span><strong style="color:#059669">{int(delta.get('recovered') or 0)}</strong> fixed</span>
            </div>
            {top_html}
            <div style="font-size:12px;color:#374151;margin-top:6px">
                Quarantine debt: <strong>{int(debt.get('active') or 0)}</strong> active
                ({int(debt.get('stale') or 0)} stale, {int(debt.get('ready_to_promote') or 0)} ready to promote)
            </div>
            {gate_html}
            {investigator_html}
        </div>""")

    # Tier 2 item 12 — retro header block prepends the AI-written
    # narrative + weekly recovery counters. Rendered only when the
    # digest was produced by ``retro_digest_service``.
    if is_retro:
        narrative = _e(digest.get("retro_narrative") or "", max_len=2000)
        released_flaky = int(digest.get("released_flaky") or 0)
        new_regressions_retro = int(digest.get("new_regressions") or 0)
        sections.append(f"""
        <div style="background:#EFF6FF;border-left:4px solid #2563EB;padding:12px 16px;margin-bottom:16px;border-radius:4px">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:#1E3A8A;font-weight:600;margin-bottom:6px">Week in review</div>
            <p style="font-size:13px;color:#1F2937;margin:0">{narrative}</p>
            <div style="display:flex;gap:16px;margin-top:10px;font-size:12px;color:#374151">
                <span><strong style="color:#059669">{released_flaky}</strong> flaky tests recovered</span>
                <span><strong style="color:#DC2626">{new_regressions_retro}</strong> new regressions vs. last week</span>
            </div>
        </div>""")

    # Header metrics
    trend_str = ""
    if trend is not None:
        sign = "+" if trend >= 0 else ""
        color = "#059669" if trend >= 0 else "#DC2626"
        trend_str = f' <span style="color:{color}">({sign}{trend:.1f}%)</span>'

    avg_pr_display = f"{avg_pr:.1f}%" if avg_pr is not None else "N/A"

    # US-12.2: hours-saved stat tile — only when the availability gate
    # passed (the key is absent otherwise).
    hours_saved = digest.get("value_headline_hours_30d")
    value_tile = (
        f"""
        <div style="background:#EEF2FF;border-radius:8px;padding:12px 16px;text-align:center;min-width:100px">
            <div style="font-size:20px;font-weight:bold;color:#4338CA">≈ {hours_saved:g} h</div>
            <div style="font-size:11px;color:#6B7280">Eng-hours saved (30d)</div>
        </div>"""
        if hours_saved is not None else ""
    )

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
        </div>{value_tile}
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

    # AI-7: weekly flaky-debt review — TEXT-ONLY per-team drafts for teams
    # without their own US-7.3 channel, rendered as escaped preformatted
    # blocks (each draft opens with its automation label).
    review_teams = (digest.get("flaky_debt_review") or {}).get("teams") or []
    review_blocks = "".join(
        "<pre style='font-size:11.5px;background:#F8FAFC;border:1px solid #E5E7EB;"
        "border-radius:6px;padding:10px;white-space:pre-wrap;margin:6px 0'>"
        f"{_e(t.get('text'), 4000)}</pre>"
        for t in review_teams if t.get("text")
    )
    if review_blocks:
        sections.append(
            "<h3 style='color:#1E40AF;font-size:14px'>Weekly flaky-debt review</h3>"
            + review_blocks
        )

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
