"""Per-test failure detail: the suite, the names, the reasons.

Built for FR-001 and FR-002, which are one problem seen from two surfaces. The
owner's report was that reports and the chat summary say *how many* tests
failed but never *which*, never the suite, and never why — "text ... which lack
credible details".

That is not the model being vague. Nothing was fetching the detail:

* ``ConversationAgent._fetch_run_context`` selected run-level aggregates only —
  build, branch, status, counts, pass rate. No per-test rows, no suite.
* ``SummaryAgent._build_context`` built its bullets as
  ``[CATEGORY] conf=N%: <summary>``, binding the test id to ``_tc_id`` and
  throwing it away.

A model cannot name a test it was never shown, and telling it to "be specific"
without supplying the rows would invite it to invent names. So this module
supplies them.

Design notes that matter:

* **Suite comes from both places.** Old live-stream runs carry the suite only on
  the run (``primary_suite_name``), newer ingests put it on the case
  (``suite_name``). Reading one gives blank suites for half the corpus, which is
  the ``_effective_suite_sql`` rule this repo already learned the hard way.
* **The cap is disclosed, not silent.** A 400-failure run cannot go in a prompt.
  Truncating quietly turns a partial view into something a reader takes as
  complete, so the payload carries ``returned`` / ``total`` / ``truncated``.
* **Confidence travels with the category.** ``INFRASTRUCTURE`` at 95 and at 30
  are different claims. The analysis may also be absent entirely — that is
  reported as absent rather than defaulted to a category.

W07 FAIL-01 adds the /failures half (:func:`latest_failure_detail_by_fingerprint`,
at the end of this module): for the fingerprints of one top-failing page, the
test's NEWEST FAILED/BROKEN row inside the request's scope (window, suite,
release) in ONE project --

* ``latest_run_id`` / ``latest_test_case_id`` / ``latest_failed_at`` -- the row,
  for "Open in run" (``/runs/{run}/tests/{case}``);
* ``latest_error`` -- the first line of its message, ``latest_stack_head`` --
  the first lines of its trace. Both are truncated HERE, before they leave the
  database, and pass ``redaction_service.redact_log_message`` (credentials and
  email markers only: ``redact_text``'s shape heuristics turn a line number
  into ``[REDACTED_PHONE]``);
* ``owner`` / ``owner_source`` / ``owner_team`` -- the stored assignee when that
  user may own work (active, not a service account, not synthetic), else the
  team the project's ownership rules give the test, else nobody (``None``,
  never a placeholder name).

One statement reads the rows (the shared suite / release fragments of
``analytics_scope``: no copy of the suite rule); the ownership rules and the
component map are read only when some row has no eligible assignee. A row of
another project is never read: the statement pins ``tr.project_id``, because
``test_fingerprint`` is not salted per project.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

import structlog
from sqlalchemy import bindparam, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AIAnalysis, Project, ServiceOwnershipRule, TestCase, TestRun
from app.services.analytics_scope import (
    ReleaseArg,
    SuiteArg,
    release_filter_sql,
    scoped_text,
    suite_filter_sql,
)
from app.services.redaction_service import redact_log_message

logger = structlog.get_logger("services.failure_detail")

# How many failing tests to describe in full. Chosen to stay inside a prompt
# budget while covering the runs people actually read; anything beyond it is
# counted and disclosed, never dropped in silence.
DEFAULT_LIMIT = 25

# Failure text is quoted into prompts and reports. Long stack traces crowd out
# everything else without adding signal past the first few lines.
MAX_ERROR_CHARS = 400


def effective_suite(case_suite: Optional[str], run_suite: Optional[str]) -> Optional[str]:
    """The suite a reader would call this test's, from either source.

    Mirrors ``analytics_service._effective_suite_sql`` in Python: the case's own
    suite wins, the run's primary suite is the fallback. Reading only one leaves
    the suite blank for a whole class of runs.
    """
    for candidate in (case_suite, run_suite):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return None


async def failure_detail(
    db: AsyncSession,
    run_id: Any,
    *,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Named failing tests for a run, with suite, reason and classification.

    Returns a dict carrying ``suite`` (the run's primary suite, when it has
    one), ``failures`` (a list, most-confident classification first), and the
    disclosure fields ``returned`` / ``total`` / ``truncated``.

    Never raises: a reporting surface losing its detail is worse than losing the
    detail *and* the report, so callers get an empty-but-honest payload.
    """
    empty: dict[str, Any] = {
        "suite": None, "failures": [], "returned": 0, "total": 0,
        "truncated": False,
    }
    try:
        run = (
            await db.execute(select(TestRun).where(TestRun.id == run_id))
        ).scalar_one_or_none()
        if run is None:
            return empty

        rows = (
            await db.execute(
                select(TestCase, AIAnalysis)
                .outerjoin(AIAnalysis, AIAnalysis.test_case_id == TestCase.id)
                .where(
                    TestCase.test_run_id == run_id,
                    TestCase.status.in_(["FAILED", "BROKEN"]),
                )
            )
        ).all()

        failures: list[dict[str, Any]] = []
        for case, analysis in rows:
            error = (case.error_message or "").strip()
            failures.append({
                # For links back to the test (the chat's source chips).
                "test_case_id": str(case.id),
                "test_name": case.test_name,
                "suite": effective_suite(case.suite_name, run.primary_suite_name),
                "status": str(case.status),
                "error_message": error[:MAX_ERROR_CHARS] or None,
                # Absent analysis is reported as absent. Defaulting to UNKNOWN
                # would be indistinguishable from a classifier that ran and
                # could not decide.
                "failure_category": (
                    str(analysis.failure_category) if analysis and analysis.failure_category
                    else None
                ),
                "confidence_score": analysis.confidence_score if analysis else None,
                "root_cause_summary": (
                    (analysis.root_cause_summary or "").strip()[:MAX_ERROR_CHARS] or None
                    if analysis else None
                ),
                "is_flaky": bool(analysis.is_flaky) if analysis else None,
            })

        # Most-confident classifications first so a truncated view keeps the
        # findings a reader can most rely on. Unclassified failures sort last
        # rather than being hidden — they are still real failures.
        failures.sort(
            key=lambda f: (f["confidence_score"] is None, -(f["confidence_score"] or 0),
                           f["test_name"] or ""),
        )
        total = len(failures)
        capped = failures[: max(1, limit)]
        return {
            "suite": run.primary_suite_name,
            "failures": capped,
            "returned": len(capped),
            "total": total,
            "truncated": total > len(capped),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("failure_detail_unavailable", run_id=str(run_id), error=str(exc)[:200])
        return empty


def format_for_prompt(detail: dict[str, Any]) -> str:
    """Render failure detail for a model or a report.

    Says plainly when there is nothing to show, and when the list was cut short.
    An unlabelled partial list is the thing this whole feature exists to stop.
    """
    failures = detail.get("failures") or []
    if not failures:
        return "Failing tests: none recorded for this run."

    lines: list[str] = []
    suite = detail.get("suite")
    if suite:
        lines.append(f"Suite: {suite}")

    header = f"Failing tests ({detail.get('returned')} of {detail.get('total')})"
    if detail.get("truncated"):
        header += (
            " — this list is TRUNCATED; do not describe it as the complete set"
        )
    lines.append(header + ":")

    for f in failures:
        bits = [f"- {f.get('test_name') or '(unnamed test)'}"]
        if f.get("suite"):
            bits.append(f"[suite: {f['suite']}]")
        if f.get("status"):
            bits.append(f"({f['status']})")
        lines.append(" ".join(bits))
        if f.get("error_message"):
            lines.append(f"    error: {f['error_message']}")
        if f.get("failure_category"):
            conf = f.get("confidence_score")
            conf_txt = f" at {conf}% confidence" if conf is not None else " (confidence unrecorded)"
            flaky = ", flagged flaky" if f.get("is_flaky") else ""
            lines.append(f"    classified: {f['failure_category']}{conf_txt}{flaky}")
        else:
            lines.append("    classified: not analysed")
        if f.get("root_cause_summary"):
            lines.append(f"    cause: {f['root_cause_summary']}")
    return "\n".join(lines)


# ── W07 FAIL-01: the latest failure of each top-failing test (/failures) ─────

#: Characters of ``error_message`` / ``stack_trace`` the statement reads.
ERROR_READ_CHARS = 1000
STACK_READ_CHARS = 4000
#: Every returned line is cut to this many characters.
LINE_CAP = 300
#: Lines of the stack trace returned.
STACK_LINES = 20

#: The keys FAIL-01 adds to a top-failing row (``analytics_service`` adds
#: ``detail_available`` beside them).
DETAIL_KEYS = (
    "latest_run_id",
    "latest_test_case_id",
    "latest_failed_at",
    "latest_error",
    "latest_stack_head",
    "owner",
    "owner_source",
    "owner_team",
)

_LINE_BREAK = re.compile(r"\r\n|\n|\r")

#: An eligible assignee (the W05 release-owner rule): active, not a service
#: account, not synthetic. NULL otherwise, and for no assignee at all.
_ASSIGNEE_NAME_SQL = (
    "CASE WHEN u.is_active IS NOT FALSE AND u.is_service_account IS NOT TRUE "
    "AND u.is_synthetic IS NOT TRUE THEN COALESCE(u.full_name, u.username) END"
)


def empty_detail() -> dict[str, Any]:
    """Every FAIL-01 detail key, ``None``: nothing located (or not asked)."""
    return {key: None for key in DETAIL_KEYS}


def first_error_line(text: str | None) -> str | None:
    """The first line of a message (split on CRLF, LF, CR), stripped, at most
    :data:`LINE_CAP` characters, redacted; ``None`` for a blank message.

    Leading blank lines are skipped (the message is stripped first, the
    ``github_checks_service._first_line`` precedent): a message that opens with
    a newline still has a first line worth showing.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None
    line = _LINE_BREAK.split(stripped, 1)[0].strip()[:LINE_CAP]
    return redact_log_message(line) or None


def stack_head(text: str | None) -> str | None:
    """The first :data:`STACK_LINES` lines of a trace, each at most
    :data:`LINE_CAP` characters, joined with ``\\n``, redacted; ``None`` when
    the trace is blank. Indentation is kept (it is how a trace reads)."""
    if not (text or "").strip():
        return None
    lines = _LINE_BREAK.split(str(text).strip("\r\n"))[:STACK_LINES]
    head = "\n".join(line[:LINE_CAP].rstrip() for line in lines)
    if not head.strip():
        return None
    return redact_log_message(head)


def _iso_utc(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return str(moment.astimezone(timezone.utc).isoformat())
    return str(value)


def _text_or_none(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


async def _rule_owner_of(
    db: AsyncSession, project_id: uuid.UUID, rows: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[str, str]]:
    """``fingerprint -> (team, owner_source)`` from the project's active
    ownership rules and component map: two statements, one project."""
    rules = (
        await db.execute(
            select(ServiceOwnershipRule)
            .where(
                ServiceOwnershipRule.project_id == project_id,
                ServiceOwnershipRule.is_active.is_(True),
            )
            .order_by(ServiceOwnershipRule.priority.desc(), ServiceOwnershipRule.created_at)
        )
    ).scalars().all()
    component_map = (
        await db.execute(select(Project.component_owner_map).where(Project.id == project_id))
    ).scalar_one_or_none()
    # Lazy: ``analytics_service`` imports this module inside ``top_failing_tests``.
    # ``_OWNER_SOURCE_FAMILY`` is the W06 defect list's ``match_source`` ->
    # ``owner_source`` mapping (any rule type -> ``rule``): one copy.
    from app.services.analytics_service import _OWNER_SOURCE_FAMILY
    from app.services.ownership_resolver_service import resolve_test_ownership

    out: dict[str, tuple[str, str]] = {}
    for row in rows:
        attrs = {
            "suite_name": row.get("suite_name") or "",
            "component": row.get("class_name") or "",
            "package": row.get("package_name") or "",
            "path": row.get("full_name") or "",
            "label": row.get("own_label") or "",
            "owner": row.get("own_owner") or "",
        }
        result = resolve_test_ownership(list(rules), attrs, component_map)
        team = str(result.team_name or "").strip()
        if team and team.lower() != "unknown" and result.match_source:
            out[row["test_fingerprint"]] = (team, _OWNER_SOURCE_FAMILY.get(result.match_source, "rule"))
    return out


async def latest_failure_detail_by_fingerprint(
    db: AsyncSession,
    project_id: uuid.UUID | str,
    fingerprints: Sequence[str],
    *,
    days: int,
    suite_name: SuiteArg = None,
    release_id: ReleaseArg = None,
    period_start: datetime | None = None,
) -> dict[str, dict]:
    """``fingerprint -> {latest_run_id, latest_test_case_id, latest_failed_at,
    latest_error, latest_stack_head, owner, owner_source, owner_team}`` for the
    fingerprints that have a FAILED/BROKEN row in ``project_id`` inside the
    window / suite / release. A fingerprint with none is absent.

    ``period_start`` is the caller's window start when it has one (the
    top-failing query's own, so both statements read the same window);
    otherwise ``now - days``. At most three statements, one when every row has
    an eligible assignee, none for no fingerprints.
    """
    fps = list(dict.fromkeys(fp for fp in fingerprints if fp))
    if not fps:
        return {}
    pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
    params: dict[str, Any] = {
        "period_start": period_start or (datetime.now(timezone.utc) - timedelta(days=days)),
        "detail_project_id": pid,
        "detail_fingerprints": fps,
    }
    suite_filter = suite_filter_sql(params, suite_name)
    release_filter = release_filter_sql(params, release_id)
    statement = scoped_text(
        f"""
        SELECT DISTINCT ON (tc.test_fingerprint)
               tc.test_fingerprint, tc.id AS test_case_id, tc.test_run_id, tc.created_at,
               left(tc.error_message, {ERROR_READ_CHARS}) AS error_head,
               left(tc.stack_trace, {STACK_READ_CHARS}) AS stack_head,
               tc.assigned_to_user_id, tc.suite_name, tc.class_name, tc.package_name, tc.full_name,
               COALESCE(tc.feature, tc.epic) AS own_label, tc.owner AS own_owner,
               {_ASSIGNEE_NAME_SQL} AS assignee_name
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        LEFT JOIN users u ON u.id = tc.assigned_to_user_id
        WHERE tr.project_id = :detail_project_id
          AND tc.test_fingerprint IN :detail_fingerprints
          AND tc.status IN ('FAILED', 'BROKEN')
          AND tc.created_at >= :period_start
          {suite_filter}
          {release_filter}
        ORDER BY tc.test_fingerprint, tc.created_at DESC, tc.id DESC
        """,
        params,
    ).bindparams(bindparam("detail_fingerprints", expanding=True))
    rows = [dict(row._mapping) for row in (await db.execute(statement, params)).fetchall()]
    if not rows:
        return {}

    unowned = [row for row in rows if not _text_or_none(row.get("assignee_name"))]
    # The rule team rides beside an assignee too (``owner_team``), but the rules
    # are read only when some row needs them to name an owner at all.
    teams = await _rule_owner_of(db, pid, rows) if unowned else {}

    out: dict[str, dict] = {}
    for row in rows:
        fp = row["test_fingerprint"]
        assignee = _text_or_none(row.get("assignee_name"))
        team: str | None
        team_source: str | None
        team, team_source = teams.get(fp, (None, None))
        owner: str | None
        owner_source: str | None
        if assignee:
            owner, owner_source = assignee, "assignee"
        elif team:
            owner, owner_source = team, team_source
        else:
            owner, owner_source = None, None
        run_id, case_id = row.get("test_run_id"), row.get("test_case_id")
        out[fp] = {
            "latest_run_id": str(run_id) if run_id is not None else None,
            "latest_test_case_id": str(case_id) if case_id is not None else None,
            "latest_failed_at": _iso_utc(row.get("created_at")),
            "latest_error": first_error_line(row.get("error_head")),
            "latest_stack_head": stack_head(row.get("stack_head")),
            "owner": owner,
            "owner_source": owner_source,
            "owner_team": team,
        }
    return out
