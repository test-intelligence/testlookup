"""The run report an email carries, so a reader need not open the dashboard.

Owner request 2026-10-10: run emails said one line ("2 failures detected in
E2E Lab (build #42)") plus four numbers. A reader had to open the app for
everything they act on: which suite, which tests, what the errors were, whether
the failures are new, what the AI concluded, and what the run does to the
release. This module gathers all of that once per run, and renders it as an
email section (HTML and plain text).

Built by the delivery relay once per run, BEFORE its concurrent fan-out, on
the relay's own session: the senders never touch the database. It is never
persisted on the notification row -- it is a projection of the run at send
time.

AI content (per-test root causes and the run's AI summary) goes through the
same review gate as every other channel (``decide_run_distribution``):
included when reviewed or not AI, marked as a draft where the project allows
drafts, withheld with a notice when the gate refuses. Everything else -- the
counts, the failing tests and their error text, the suites, the release gate --
is deterministic and always included.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from html import escape
from typing import Any, Optional

import structlog
from sqlalchemy import func, select

from app.models.postgres import AIAnalysis, Project, Release, TestCase, TestRun, User
from app.services.redaction_service import redact_log_message

logger = structlog.get_logger(__name__)

#: Bounds that keep an email readable and well under provider size limits.
MAX_FAILURES = 25
MAX_SUITES = 15
ERROR_CHARS = 400
ROOT_CAUSE_CHARS = 400
MAX_BLOCKING_REASONS = 8
#: A live gate preview reads every run of the release; above this many runs
#: the email uses the stored verdict instead of holding up delivery. (Sized,
#: not timed: cancelling an in-flight query can leave the connection unusable.)
GATE_PREVIEW_MAX_RUNS = 200

_FAILING = ("FAILED", "BROKEN")
AI_WITHHELD_NOTICE = (
    "AI analysis for this run is awaiting human review, so its conclusions are "
    "not included here."
)


def _status_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").upper()


def _clip(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else None


def _safe_url(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text if text.lower().startswith(("http://", "https://")) else None


async def build_run_report(
    db: Any,
    run_id: Any,
    *,
    base_url: str,
    channel: str = "email_run_report",
) -> tuple[Optional[dict[str, Any]], Any]:
    """Gather the report for one run. Returns ``(report, distribution_decision)``.

    ``None`` when the run no longer exists. Never raises for a missing optional
    part (AI summary, release, previous run): those sections are left out.
    The decision is returned so the caller can record its audit row after
    closing its own session (``record_distribution_detached``).
    """
    try:
        run_uuid = run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(str(run_id))
    except (TypeError, ValueError):
        return None, None
    run = await db.get(TestRun, run_uuid)
    if run is None:
        return None, None
    project = await db.get(Project, run.project_id)
    base = base_url.rstrip("/")

    report: dict[str, Any] = {
        "project": {"id": str(run.project_id), "name": getattr(project, "name", None)},
        "run": _run_facts(run),
        "counts": _counts(run),
        "links": {
            "run": f"{base}/runs/{run.id}",
            "failures": f"{base}/runs/{run.id}?status=FAILED",
            "analysis": f"{base}/runs/{run.id}?tab=analysis",
            "release_gate": f"{base}/release-gate/{run.id}",
        },
    }

    previous = await _previous_run(db, run)
    if previous is not None:
        report["previous"] = {
            "build_number": previous.build_number,
            "pass_rate": previous.pass_rate,
            "failed": int(previous.failed_tests or 0) + int(previous.broken_tests or 0),
            "link": f"{base}/runs/{previous.id}",
        }
    previous_failing = await _failing_fingerprints(db, previous.id) if previous is not None else set()

    report["suites"] = await _suites(db, run.id)
    failures, failing_total = await _failures(db, run.id, base, previous_failing, has_previous=previous is not None)
    report["failures"] = failures
    report["failing_total"] = failing_total
    report["categories"] = await _categories(db, run.id)
    report["new_failures"] = sum(1 for f in failures if f.get("is_new"))

    decision = None
    ai_allowed = True
    try:
        from app.services.report_distribution_policy import decide_run_distribution  # noqa: PLC0415

        decision = await decide_run_distribution(
            db, run_id=run.id, project_id=run.project_id, channel=channel
        )
        ai_allowed = decision.allowed
        report["ai_watermark"] = decision.watermark
    except Exception as exc:  # noqa: BLE001 -- fail closed while the gate is enforced
        from app.services.report_distribution_policy import gate_enforced  # noqa: PLC0415

        ai_allowed = not gate_enforced()
        logger.warning("run_report_gate_unavailable", run_id=str(run.id), error_type=type(exc).__name__)
    report["ai_allowed"] = ai_allowed
    if ai_allowed:
        await _attach_root_causes(db, failures)
        report["ai_summary"] = await _ai_summary(run.id)
    else:
        report["ai_withheld"] = AI_WITHHELD_NOTICE

    if run.primary_release_id is not None:
        report["release"] = await _release_impact(
            db, run.primary_release_id, base, project_id=run.project_id,
            run_failing=await _failing_fingerprints(db, run.id),
        )

    return report, decision


def _run_facts(run: TestRun) -> dict[str, Any]:
    started = run.start_time or run.created_at
    return {
        "id": str(run.id),
        "build_number": run.build_number,
        "status": _status_value(run.status),
        "suite": run.primary_suite_name,
        "branch": run.branch,
        "commit": (run.commit_hash or "")[:12] or None,
        "environment": run.environment,
        "framework": getattr(run, "framework", None),
        "trigger": run.trigger_source,
        "source": run.ingestion_source,
        "ci_provider": run.ci_provider,
        "ci_repo": run.ci_repo,
        "pr_number": run.pr_number,
        "ci_actor": run.ci_actor,
        "ci_run_url": _safe_url(run.ci_run_url),
        "jenkins_job": run.jenkins_job,
        "started_at": _iso(started),
        "finished_at": _iso(run.end_time),
        "duration_ms": run.duration_ms,
        "ingestion_complete": run.ingestion_complete,
        "rejected_results": int(run.ingestion_rejected_tests or 0),
    }


def _counts(run: TestRun) -> dict[str, Any]:
    return {
        "total": int(run.total_tests or 0),
        "passed": int(run.passed_tests or 0),
        "failed": int(run.failed_tests or 0),
        "broken": int(run.broken_tests or 0),
        "skipped": int(run.skipped_tests or 0),
        "pass_rate": run.pass_rate,
    }


async def _previous_run(db: Any, run: TestRun) -> Optional[TestRun]:
    """The run before this one: same project, created earlier, same branch when
    the branch is known (else any branch)."""
    stmt = (
        select(TestRun)
        .where(
            TestRun.project_id == run.project_id,
            TestRun.id != run.id,
            TestRun.created_at < run.created_at,
        )
        .order_by(TestRun.created_at.desc())
        .limit(1)
    )
    if run.branch:
        same_branch: Optional[TestRun] = (
            await db.execute(stmt.where(TestRun.branch == run.branch))
        ).scalars().first()
        if same_branch is not None:
            return same_branch
    any_branch: Optional[TestRun] = (await db.execute(stmt)).scalars().first()
    return any_branch


async def _failing_fingerprints(db: Any, run_id: uuid.UUID) -> set[str]:
    rows = await db.execute(
        select(TestCase.test_fingerprint).where(
            TestCase.test_run_id == run_id, TestCase.status.in_(_FAILING)
        )
    )
    return {fp for (fp,) in rows.all() if fp}


async def _suites(db: Any, run_id: uuid.UUID) -> list[dict[str, Any]]:
    status = TestCase.status
    rows = await db.execute(
        select(
            TestCase.suite_name,
            func.count(),
            func.count().filter(status == "PASSED"),
            func.count().filter(status == "FAILED"),
            func.count().filter(status == "BROKEN"),
            func.count().filter(status == "SKIPPED"),
        )
        .where(TestCase.test_run_id == run_id)
        .group_by(TestCase.suite_name)
    )
    suites = [
        {"name": name or "(no suite)", "total": t, "passed": p, "failed": f, "broken": b, "skipped": s}
        for name, t, p, f, b, s in rows.all()
    ]
    suites.sort(key=lambda s: (-(s["failed"] + s["broken"]), -s["total"], s["name"]))
    return suites[:MAX_SUITES]


async def _failures(
    db: Any,
    run_id: uuid.UUID,
    base: str,
    previous_failing: set[str],
    *,
    has_previous: bool,
) -> tuple[list[dict[str, Any]], int]:
    total = (
        await db.execute(
            select(func.count()).where(
                TestCase.test_run_id == run_id, TestCase.status.in_(_FAILING)
            )
        )
    ).scalar_one()
    rows = await db.execute(
        select(
            TestCase.id, TestCase.test_name, TestCase.suite_name, TestCase.class_name,
            TestCase.status, TestCase.duration_ms, TestCase.error_message,
            TestCase.failure_category, TestCase.test_fingerprint, TestCase.tags,
            User.username,
        )
        .outerjoin(User, User.id == TestCase.assigned_to_user_id)
        .where(TestCase.test_run_id == run_id, TestCase.status.in_(_FAILING))
        .order_by(TestCase.status.desc(), TestCase.suite_name, TestCase.test_name)
        .limit(MAX_FAILURES)
    )
    failures = []
    for tc_id, name, suite, cls, status, duration, error, category, fp, tags, assignee in rows.all():
        tag_list = tags if isinstance(tags, list) else []
        failures.append({
            "id": str(tc_id),
            "name": name,
            "suite": suite,
            "class_name": cls,
            "status": _status_value(status),
            "duration_ms": duration,
            # Error text leaves the system by email: credentials and email
            # addresses are redacted. (Not the shape heuristics of redact_text,
            # which turn an assertion's expected number into [REDACTED_PHONE].)
            "error": _clip(redact_log_message(str(error or "")), ERROR_CHARS) or None,
            "category": _status_value(category) or None,
            "is_new": (fp not in previous_failing) if has_previous else None,
            "quarantined": "quarantined" in tag_list,
            "flaky": "flaky" in tag_list,
            "assignee": assignee,
            "link": f"{base}/runs/{run_id}/tests/{tc_id}",
        })
    return failures, int(total or 0)


async def _categories(db: Any, run_id: uuid.UUID) -> dict[str, int]:
    rows = await db.execute(
        select(TestCase.failure_category, func.count())
        .where(TestCase.test_run_id == run_id, TestCase.status.in_(_FAILING))
        .group_by(TestCase.failure_category)
    )
    out: dict[str, int] = {}
    for cat, n in rows.all():
        key = _status_value(cat) or "UNKNOWN"
        out[key] = out.get(key, 0) + int(n)
    return out


async def _attach_root_causes(db: Any, failures: list[dict[str, Any]]) -> None:
    """The AI's root cause per failing test, when there is a usable one."""
    ids = [uuid.UUID(f["id"]) for f in failures]
    if not ids:
        return
    rows = await db.execute(
        select(AIAnalysis.test_case_id, AIAnalysis.root_cause_summary, AIAnalysis.confidence_score,
               AIAnalysis.failure_category)
        .where(AIAnalysis.test_case_id.in_(ids))
    )
    by_case = {str(tc): (cause, conf, cat) for tc, cause, conf, cat in rows.all()}
    for failure in failures:
        cause, conf, cat = by_case.get(failure["id"], (None, None, None))
        text = str(cause or "").strip()
        # A failed explanation step is not a root cause (summary agent, same rule).
        if text and not text.startswith("AI analysis could not complete."):
            failure["ai_root_cause"] = _clip(text, ROOT_CAUSE_CHARS)
            failure["ai_confidence"] = conf
        if not failure.get("category") and cat:
            failure["category"] = _status_value(cat)


async def _ai_summary(run_id: uuid.UUID) -> Optional[dict[str, Any]]:
    try:
        from app.db.mongo import Collections, get_mongo_db  # noqa: PLC0415

        doc = await get_mongo_db()[Collections.RUN_SUMMARIES].find_one({"test_run_id": str(run_id)})
    except Exception as exc:  # noqa: BLE001 -- an email without the summary still goes
        logger.warning("run_report_summary_unavailable", run_id=str(run_id), error_type=type(exc).__name__)
        return None
    if not doc:
        return None
    panel = doc.get("executive_panel") if isinstance(doc.get("executive_panel"), dict) else {}
    summary = doc.get("executive_summary") or doc.get("layer1_executive_summary")
    if not summary and not panel:
        return None
    def _texts(value: Any) -> list[str]:
        items = value if isinstance(value, list) else []
        return [_clip(t.get("text") if isinstance(t, dict) else t, 300) for t in items[:6] if t]

    dominant = panel.get("dominant_failure")
    return {
        "headline": _clip(panel.get("headline"), 300) if panel.get("headline") else None,
        "summary": _clip(summary, 2000) if summary else None,
        "takeaways": _texts(panel.get("key_takeaways")),
        "actions": _texts(panel.get("next_actions")),
        "signal": panel.get("status_signal"),
        "risk_score": panel.get("risk_score"),
        "dominant_failure": _clip(dominant.get("summary") or dominant.get("label") or "", 300)
        if isinstance(dominant, dict) else (_clip(dominant, 300) if dominant else None),
        "generated_at": _iso(doc.get("generated_at")) if isinstance(doc.get("generated_at"), datetime) else doc.get("generated_at"),
    }


def _reason_key(reason: str) -> tuple[Optional[str], Optional[str], str]:
    """``"<suite>::<fingerprint> is FAILED"`` -> (suite, fingerprint, status)."""
    head, sep, status = reason.rpartition(" is ")
    if not sep or "::" not in head:
        return None, None, reason
    suite, _, ident = head.rpartition("::")
    return suite, ident, status


async def _readable_reasons(db: Any, project_id: Any, reasons: list[str]) -> list[str]:
    """The gate names a test by suite and fingerprint; an email names it by
    test name. Defect reasons, and any key that does not resolve, pass through."""
    fps = {fp for _, fp, _ in map(_reason_key, reasons) if fp}
    names: dict[str, str] = {}
    if fps:
        rows = await db.execute(
            select(TestCase.test_fingerprint, func.max(TestCase.test_name))
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(TestRun.project_id == project_id, TestCase.test_fingerprint.in_(fps))
            .group_by(TestCase.test_fingerprint)
        )
        names = {fp: name for fp, name in rows.all() if name}
    out = []
    for reason in reasons:
        suite, fp, status = _reason_key(reason)
        if fp and fp in names:
            out.append(f"{names[fp]} ({suite or 'no suite'}) is {status}")
        else:
            out.append(reason)
    return out


async def _release_impact(
    db: Any,
    release_id: uuid.UUID,
    base: str,
    *,
    project_id: Any,
    run_failing: set[str],
) -> Optional[dict[str, Any]]:
    release = await db.get(Release, release_id)
    if release is None:
        return None
    from app.services import release_gate_service  # noqa: PLC0415

    gate: Optional[dict[str, Any]] = None
    live = False
    run_count = (
        await db.execute(select(func.count()).where(TestRun.primary_release_id == release_id))
    ).scalar_one()
    if int(run_count or 0) <= GATE_PREVIEW_MAX_RUNS:
        gate = await release_gate_service.evaluate_release(db, release_id, record=False)
        live = True
    else:
        gate = await release_gate_service.current_gate(db, release_id)
    impact: dict[str, Any] = {
        "name": release.name,
        "version": getattr(release, "version", None),
        "status": release.status,
        "link": f"{base}/releases/{release.id}",
    }
    if gate:
        card = gate.get("scorecard") or {}
        reasons = [str(r) for r in (gate.get("blocking_reasons") or [])]
        # Failures of THIS run first: that is what the reader is being told about.
        from_this_run = [r for r in reasons if _reason_key(r)[1] in run_failing]
        ordered = from_this_run + [r for r in reasons if r not in from_this_run]
        impact.update({
            "verdict": gate.get("verdict"),
            "live": live,
            "decided_at": gate.get("decided_at"),
            "blocking_count": len(reasons),
            "blocking_from_this_run": len(from_this_run),
            "blocking_reasons": [
                _clip(r, 200)
                for r in await _readable_reasons(db, project_id, ordered[:MAX_BLOCKING_REASONS])
            ],
            "pass_rate": card.get("pass_rate"),
            "distinct_tests": card.get("denominator"),
            "evidence_count": card.get("evidence_count"),
            "run_count": card.get("run_count"),
            "measured": card.get("measured"),
            "quarantined_failures": len(card.get("quarantined_failures") or []),
            "insufficient_reason": card.get("insufficient_reason"),
        })
        defects = gate.get("defects")
        if isinstance(defects, dict):
            impact["open_defects"] = defects.get("open_total")
            impact["blocking_defects"] = defects.get("blocking_count")
    return impact


# ── Rendering ────────────────────────────────────────────────────────────────

_C = {
    "text": "#e2e8f0", "muted": "#94a3b8", "faint": "#64748b", "panel": "#0f172a",
    "border": "#334155", "fail": "#ef4444", "broken": "#f59e0b", "pass": "#22c55e",
    "accent": "#60a5fa",
}
_VERDICT_COLOURS = {"GO": "#22c55e", "NO_GO": "#ef4444", "NOT_EVALUATED": "#f59e0b"}


def _fmt_duration(ms: Any) -> Optional[str]:
    if not isinstance(ms, (int, float)) or ms < 0:
        return None
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m {sec}s"


def _fmt_rate(rate: Any) -> str:
    return f"{rate:.1f}%" if isinstance(rate, (int, float)) else "—"


def _fmt_time(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return str(value)


def _overview_rows(report: dict[str, Any]) -> list[tuple[str, str]]:
    run = report.get("run") or {}
    project = report.get("project") or {}
    release = report.get("release") or {}
    rows: list[tuple[str, Optional[str]]] = [
        ("Project", project.get("name")),
        ("Build", f"#{run['build_number']}" if run.get("build_number") else None),
        ("Run status", run.get("status")),
        ("Test suite", run.get("suite")),
        ("Release", " ".join(str(v) for v in (release.get("name"), f"({release['status']})" if release.get("status") else None) if v) or None),
        ("Branch", run.get("branch")),
        ("Commit", run.get("commit")),
        ("Environment", run.get("environment")),
        ("Framework", run.get("framework")),
        ("CI", " · ".join(str(v) for v in (run.get("ci_provider"), run.get("ci_repo"), f"PR #{run['pr_number']}" if run.get("pr_number") else None, run.get("jenkins_job")) if v) or None),
        ("Triggered by", " · ".join(str(v) for v in (run.get("trigger"), run.get("ci_actor")) if v) or None),
        ("Started", _fmt_time(run.get("started_at"))),
        ("Finished", _fmt_time(run.get("finished_at"))),
        ("Duration", _fmt_duration(run.get("duration_ms"))),
        ("Ingestion", f"{run['rejected_results']} result(s) rejected — this run is incomplete" if run.get("rejected_results") else None),
    ]
    return [(label, value) for label, value in rows if value]


def _delta_text(report: dict[str, Any]) -> Optional[str]:
    prev = report.get("previous")
    rate = (report.get("counts") or {}).get("pass_rate")
    if not prev or not isinstance(rate, (int, float)) or not isinstance(prev.get("pass_rate"), (int, float)):
        return None
    delta = rate - prev["pass_rate"]
    direction = "up" if delta > 0 else "down" if delta < 0 else "unchanged"
    change = f"{abs(delta):.1f} pts {direction}" if delta else "unchanged"
    return f"Pass rate {change} vs build #{prev['build_number']} ({_fmt_rate(prev['pass_rate'])}, {prev['failed']} failing)"


def render_run_report_html(report: dict[str, Any], *, include_ai_summary: bool = True) -> str:
    """The run report as an email-safe HTML section (inline styles, tables)."""
    if not report:
        return ""
    e = lambda v: escape(str(v), quote=True)  # noqa: E731
    parts: list[str] = []

    def heading(text: str) -> None:
        parts.append(
            f'<p style="margin:22px 0 8px;font-size:12px;font-weight:700;letter-spacing:.06em;'
            f'text-transform:uppercase;color:{_C["muted"]};">{e(text)}</p>'
        )

    # Results strip
    counts = report.get("counts") or {}
    cells = [
        ("Total", counts.get("total"), _C["text"]), ("Passed", counts.get("passed"), _C["pass"]),
        ("Failed", counts.get("failed"), _C["fail"]), ("Broken", counts.get("broken"), _C["broken"]),
        ("Skipped", counts.get("skipped"), _C["muted"]), ("Pass rate", _fmt_rate(counts.get("pass_rate")), _C["text"]),
    ]
    tds = "".join(
        f'<td style="padding:10px 6px;text-align:center;background:{_C["panel"]};border:1px solid {_C["border"]};">'
        f'<div style="font-size:18px;font-weight:700;color:{colour};">{e(value if value is not None else "—")}</div>'
        f'<div style="font-size:11px;color:{_C["muted"]};">{e(label)}</div></td>'
        for label, value, colour in cells
    )
    heading("Results")
    parts.append(f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;"><tr>{tds}</tr></table>')
    delta = _delta_text(report)
    if delta:
        parts.append(f'<p style="margin:8px 0 0;font-size:13px;color:{_C["muted"]};">{e(delta)}</p>')

    # Run details
    heading("Run details")
    rows = "".join(
        f'<tr><td style="padding:4px 10px 4px 0;font-size:13px;color:{_C["muted"]};white-space:nowrap;vertical-align:top;">{e(label)}</td>'
        f'<td style="padding:4px 0;font-size:13px;color:{_C["text"]};">{e(value)}</td></tr>'
        for label, value in _overview_rows(report)
    )
    parts.append(f'<table cellpadding="0" cellspacing="0" style="width:100%;">{rows}</table>')
    ci_url = (report.get("run") or {}).get("ci_run_url")
    if ci_url:
        parts.append(f'<p style="margin:6px 0 0;font-size:13px;"><a href="{e(ci_url)}" style="color:{_C["accent"]};">CI job →</a></p>')

    # Release impact
    release = report.get("release")
    if release:
        heading("Release impact")
        verdict = release.get("verdict")
        if verdict:
            colour = _VERDICT_COLOURS.get(verdict, _C["text"])
            basis = "live preview including this run" if release.get("live") else f"last evaluated {_fmt_time(release.get('decided_at')) or ''}".strip()
            parts.append(
                f'<p style="margin:0 0 6px;font-size:14px;color:{_C["text"]};">Release <strong>{e(release.get("name"))}</strong> gate: '
                f'<strong style="color:{colour};">{e(verdict.replace("_", " "))}</strong> '
                f'<span style="color:{_C["muted"]};font-size:12px;">({e(basis)})</span></p>'
            )
            facts = []
            if release.get("pass_rate") is not None:
                facts.append(f"release pass rate {_fmt_rate(release['pass_rate'])}")
            if release.get("distinct_tests") is not None:
                facts.append(f"{release['distinct_tests']} distinct tests over {release.get('run_count') or 0} runs")
            facts.append(f"{release.get('blocking_count', 0)} blocking")
            if release.get("blocking_from_this_run"):
                facts.append(f"{release['blocking_from_this_run']} of them failing in this run")
            if release.get("quarantined_failures"):
                facts.append(f"{release['quarantined_failures']} quarantined failure(s) set aside")
            if release.get("open_defects") is not None:
                facts.append(f"{release['open_defects']} open defect(s), {release.get('blocking_defects') or 0} blocking")
            if release.get("measured") is False and release.get("insufficient_reason"):
                facts.append(f"not measured: {release['insufficient_reason']}")
            parts.append(f'<p style="margin:0 0 6px;font-size:13px;color:{_C["muted"]};">{e("; ".join(facts))}</p>')
            if release.get("blocking_reasons"):
                items = "".join(f'<li style="margin:2px 0;">{e(r)}</li>' for r in release["blocking_reasons"])
                more = release.get("blocking_count", 0) - len(release["blocking_reasons"])
                if more > 0:
                    items += f'<li style="margin:2px 0;color:{_C["faint"]};">…and {more} more</li>'
                parts.append(f'<ul style="margin:4px 0 0;padding-left:18px;font-size:12px;color:{_C["text"]};">{items}</ul>')
        else:
            parts.append(f'<p style="margin:0;font-size:13px;color:{_C["muted"]};">Release {e(release.get("name"))}: the gate has not been evaluated yet.</p>')

    # AI summary
    ai_summary = report.get("ai_summary") if include_ai_summary else None
    if report.get("ai_withheld"):
        heading("AI analysis")
        parts.append(f'<p style="margin:0;font-size:13px;color:{_C["muted"]};">{e(report["ai_withheld"])}</p>')
    elif ai_summary:
        heading("AI summary")
        if report.get("ai_watermark"):
            parts.append(f'<p style="margin:0 0 6px;font-size:12px;color:{_C["broken"]};">{e(report["ai_watermark"])}</p>')
        if ai_summary.get("headline"):
            parts.append(f'<p style="margin:0 0 6px;font-size:14px;font-weight:600;color:{_C["text"]};">{e(ai_summary["headline"])}</p>')
        signal_bits = [str(v) for v in (ai_summary.get("signal"), f"risk {ai_summary['risk_score']}/100" if ai_summary.get("risk_score") is not None else None) if v]
        if signal_bits:
            parts.append(f'<p style="margin:0 0 6px;font-size:12px;color:{_C["muted"]};">Signal: {e(" · ".join(signal_bits))}</p>')
        if ai_summary.get("summary"):
            parts.append(f'<p style="margin:0 0 8px;font-size:14px;line-height:1.55;color:{_C["text"]};">{e(ai_summary["summary"])}</p>')
        if ai_summary.get("dominant_failure"):
            parts.append(f'<p style="margin:0 0 6px;font-size:13px;color:{_C["text"]};"><span style="color:{_C["muted"]};">Dominant failure:</span> {e(ai_summary["dominant_failure"])}</p>')
        for label, key in (("Key takeaways", "takeaways"), ("Recommended actions", "actions")):
            if ai_summary.get(key):
                items = "".join(f'<li style="margin:2px 0;">{e(t)}</li>' for t in ai_summary[key])
                parts.append(f'<p style="margin:6px 0 2px;font-size:13px;color:{_C["muted"]};">{e(label)}</p>'
                             f'<ul style="margin:0;padding-left:18px;font-size:13px;color:{_C["text"]};">{items}</ul>')
        parts.append(f'<p style="margin:6px 0 0;font-size:11px;color:{_C["faint"]};">AI-generated. Verify before acting.</p>')

    # Failures
    failures = report.get("failures") or []
    if failures:
        total = report.get("failing_total") or len(failures)
        new = report.get("new_failures") or 0
        title = f"Failing tests ({total})" + (f" · {new} new since the previous build" if report.get("previous") else "")
        heading(title)
        cats = report.get("categories") or {}
        if cats:
            parts.append(
                f'<p style="margin:0 0 8px;font-size:12px;color:{_C["muted"]};">By category: '
                + e(", ".join(f"{k.replace('_', ' ').lower()} {v}" for k, v in sorted(cats.items(), key=lambda kv: -kv[1])))
                + "</p>"
            )
        for f in failures:
            colour = _C["fail"] if f["status"] == "FAILED" else _C["broken"]
            badges = [f["status"]]
            if f.get("is_new") is True:
                badges.append("NEW")
            elif f.get("is_new") is False:
                badges.append("still failing")
            if f.get("quarantined"):
                badges.append("quarantined")
            if f.get("flaky"):
                badges.append("flaky")
            if f.get("category"):
                badges.append(f["category"].replace("_", " ").lower())
            meta = " · ".join(str(v) for v in (f.get("suite"), f.get("class_name"), _fmt_duration(f.get("duration_ms")), f"owner {f['assignee']}" if f.get("assignee") else None) if v)
            block = (
                f'<div style="margin:0 0 10px;padding:10px 12px;background:{_C["panel"]};border-left:3px solid {colour};border-radius:4px;">'
                f'<div style="font-size:13px;font-weight:600;color:{_C["text"]};"><a href="{e(f["link"])}" style="color:{_C["text"]};text-decoration:none;">{e(f["name"])}</a></div>'
                f'<div style="font-size:11px;color:{colour};margin-top:2px;">{e(" · ".join(badges))}</div>'
                + (f'<div style="font-size:12px;color:{_C["muted"]};margin-top:2px;">{e(meta)}</div>' if meta else "")
                + (f'<div style="font-size:12px;color:{_C["text"]};margin-top:6px;font-family:Consolas,Menlo,monospace;white-space:pre-wrap;">{e(f["error"])}</div>' if f.get("error") else "")
                + (f'<div style="font-size:12px;color:{_C["accent"]};margin-top:6px;">AI root cause'
                   + (f' ({e(f["ai_confidence"])}% confidence)' if f.get("ai_confidence") is not None else "")
                   + f': <span style="color:{_C["text"]};">{e(f["ai_root_cause"])}</span></div>' if f.get("ai_root_cause") else "")
                + "</div>"
            )
            parts.append(block)
        if total > len(failures):
            parts.append(f'<p style="margin:0;font-size:12px;color:{_C["faint"]};">…and {total - len(failures)} more failing test(s) in the run.</p>')

    # Suites
    suites = report.get("suites") or []
    if suites:
        heading("By test suite")
        head = "".join(f'<th style="padding:4px 6px;text-align:{"left" if i == 0 else "right"};font-size:11px;color:{_C["muted"]};font-weight:600;">{e(h)}</th>'
                       for i, h in enumerate(("Suite", "Total", "Passed", "Failed", "Broken", "Skipped")))
        body = "".join(
            "<tr>" + "".join(
                f'<td style="padding:4px 6px;font-size:12px;border-top:1px solid {_C["border"]};text-align:{"left" if i == 0 else "right"};'
                f'color:{(_C["fail"] if i == 3 and v else _C["broken"] if i == 4 and v else _C["text"])};">{e(v)}</td>'
                for i, v in enumerate((s["name"], s["total"], s["passed"], s["failed"], s["broken"], s["skipped"]))
            ) + "</tr>"
            for s in suites
        )
        parts.append(f'<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;"><tr>{head}</tr>{body}</table>')

    # Links
    links = report.get("links") or {}
    anchors = [
        (label, links.get(key)) for label, key in (("Open the run", "run"), ("Failing tests", "failures"), ("AI analysis", "analysis"), ("Release gate", "release_gate"))
    ]
    if release and release.get("link"):
        anchors.append(("Release", release["link"]))
    prev = report.get("previous")
    if prev and prev.get("link"):
        anchors.append((f"Previous build #{prev['build_number']}", prev["link"]))
    link_html = " · ".join(f'<a href="{e(url)}" style="color:{_C["accent"]};">{e(label)}</a>' for label, url in anchors if url)
    if link_html:
        parts.append(f'<p style="margin:20px 0 0;font-size:13px;">{link_html}</p>')
    return "".join(parts)


def render_run_report_text(report: dict[str, Any], *, include_ai_summary: bool = True) -> str:
    """The same report as plain text, for the text/plain alternative."""
    if not report:
        return ""
    lines: list[str] = []
    counts = report.get("counts") or {}
    lines += ["", "RESULTS",
              f"Total {counts.get('total', 0)} · passed {counts.get('passed', 0)} · failed {counts.get('failed', 0)} · "
              f"broken {counts.get('broken', 0)} · skipped {counts.get('skipped', 0)} · pass rate {_fmt_rate(counts.get('pass_rate'))}"]
    delta = _delta_text(report)
    if delta:
        lines.append(delta)
    lines += ["", "RUN DETAILS"] + [f"{label}: {value}" for label, value in _overview_rows(report)]
    if (report.get("run") or {}).get("ci_run_url"):
        lines.append(f"CI job: {report['run']['ci_run_url']}")
    release = report.get("release")
    if release:
        lines += ["", "RELEASE IMPACT"]
        if release.get("verdict"):
            lines.append(f"Release {release.get('name')} gate: {release['verdict'].replace('_', ' ')} "
                         f"({'live preview including this run' if release.get('live') else 'last evaluated ' + (_fmt_time(release.get('decided_at')) or '')})")
            lines.append(
                f"{release.get('blocking_count', 0)} blocking"
                + (f" ({release['blocking_from_this_run']} failing in this run)" if release.get("blocking_from_this_run") else "")
                + f"; release pass rate {_fmt_rate(release.get('pass_rate'))}"
            )
            lines += [f"  - {r}" for r in release.get("blocking_reasons") or []]
        else:
            lines.append(f"Release {release.get('name')}: the gate has not been evaluated yet.")
    if report.get("ai_withheld"):
        lines += ["", "AI ANALYSIS", report["ai_withheld"]]
    elif include_ai_summary and report.get("ai_summary"):
        ai = report["ai_summary"]
        lines += ["", "AI SUMMARY (AI-generated; verify before acting)"]
        if report.get("ai_watermark"):
            lines.append(report["ai_watermark"])
        if ai.get("headline"):
            lines.append(ai["headline"])
        if ai.get("signal") or ai.get("risk_score") is not None:
            lines.append(" · ".join(str(v) for v in (ai.get("signal"), f"risk {ai['risk_score']}/100" if ai.get("risk_score") is not None else None) if v))
        if ai.get("summary"):
            lines.append(ai["summary"])
        if ai.get("dominant_failure"):
            lines.append(f"Dominant failure: {ai['dominant_failure']}")
        for label, key in (("Key takeaways", "takeaways"), ("Recommended actions", "actions")):
            if ai.get(key):
                lines.append(f"{label}:")
                lines += [f"  - {t}" for t in ai[key]]
    failures = report.get("failures") or []
    if failures:
        lines += ["", f"FAILING TESTS ({report.get('failing_total') or len(failures)})"]
        for f in failures:
            tags = [f["status"]] + (["NEW"] if f.get("is_new") is True else ["still failing"] if f.get("is_new") is False else [])
            tags += [t for t in ("quarantined", "flaky") if f.get(t)]
            if f.get("category"):
                tags.append(f["category"].lower())
            lines.append(f"- {f['name']} [{', '.join(tags)}] — {f.get('suite') or '(no suite)'}")
            if f.get("error"):
                lines.append(f"    {f['error']}")
            if f.get("ai_root_cause"):
                lines.append(f"    AI root cause: {f['ai_root_cause']}")
            lines.append(f"    {f['link']}")
        more = (report.get("failing_total") or 0) - len(failures)
        if more > 0:
            lines.append(f"…and {more} more failing test(s).")
    suites = report.get("suites") or []
    if suites:
        lines += ["", "BY TEST SUITE"] + [
            f"- {s['name']}: {s['total']} total, {s['passed']} passed, {s['failed']} failed, {s['broken']} broken, {s['skipped']} skipped"
            for s in suites
        ]
    links = report.get("links") or {}
    if links.get("run"):
        lines += ["", f"Open the run: {links['run']}"]
    return "\n".join(lines)
