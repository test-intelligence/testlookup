"""Read-only tools for the Ask-AI chat (Agentic plan AI-6).

Ten thin, READ-ONLY wrappers over existing services/queries that the chat
agent (``app.agents.conversation``) calls through the provider's native tool
calling. Three invariants are load-bearing:

* **Tenancy by ContextVar** (same pattern as ``app.tools.recall_memory``,
  AI-F3): the project scope is bound SERVER-SIDE by the conversation agent
  before the loop starts. The LLM's tool input is free text only — never an
  identifier. Without a bound project context every tool answers
  "unavailable" instead of falling back to an unscoped query.

* **Budgets**: each call's output is truncated to a per-call token cap, and
  a shared total observation budget is decremented per call. Once the total
  budget is exhausted, tools return a fixed "budget exhausted" note so the
  loop is nudged to answer with what it has.

* **Never raises into the loop**: every failure path returns a string.

Each successful call also records a ``{tool, summary}`` trace entry (the
"How I looked this up" transparency payload) and may record structured
*findings* the conversation agent turns into ``suggested_actions``
(quarantine / Jira handoffs — a human submits, the agent never does).

The tool shapes intentionally mirror the read tools on the MCP server
(``mcp/tools/runs.py`` / ``quarantine.py`` / ``release.py``) but are
reimplemented over backend services — mcp/ code is never imported here.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog
from langchain_core.tools import tool

from app.core.config import settings
from app.services.resilience import estimate_token_count, truncate_to_token_budget

logger = structlog.get_logger("tools.chat_read_tools")

_BUDGET_EXHAUSTED_NOTE = (
    "Tool budget exhausted for this question — answer now using the "
    "observations you already collected."
)
_NO_CONTEXT_NOTE = (
    "Tool unavailable: no project context bound to this conversation "
    "(chat tools are project-scoped)."
)


# ── Context / budget state ───────────────────────────────────────────────────


@dataclass
class ChatToolState:
    """Server-side state for one chat tool loop run."""

    project_id: str
    token_budget_remaining: int
    per_call_token_cap: int
    trace: list[dict[str, str]] = field(default_factory=list)
    # Structured findings for suggested_actions. Keyed by fingerprint so
    # repeat sightings dedupe naturally.
    quarantine_candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    jira_candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Runs and tests the tools read, keyed "type:id", for the answer's linked
    # source chips ({type: test_run, id, build} / {type: test_case, id,
    # run_id, name}). Only ids the server read; never model-supplied.
    refs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add_ref(self, ref: dict[str, Any]) -> None:
        if ref.get("id"):
            self.refs.setdefault(f"{ref.get('type')}:{ref['id']}", ref)


_CHAT_TOOL_CONTEXT: ContextVar[Optional[ChatToolState]] = ContextVar(
    "chat_tool_context", default=None,
)


def default_total_token_budget() -> int:
    """Total observation budget across the whole loop."""
    return max(512, (settings.LLM_MAX_TOKENS - settings.PROMPT_OVERHEAD_TOKENS) // 2)


def set_chat_tool_context(
    *,
    project_id: str,
    total_token_budget: Optional[int] = None,
    per_call_token_cap: Optional[int] = None,
) -> Token:
    """Bind the chat loop's tenancy + budget state.

    Called by ``ConversationAgent`` before the ReAct executor starts; the
    returned token MUST be reset in a ``finally`` so concurrent chats never
    see each other's project scope.
    """
    total = total_token_budget or default_total_token_budget()
    state = ChatToolState(
        project_id=project_id,
        token_budget_remaining=total,
        per_call_token_cap=per_call_token_cap or max(256, total // 4),
    )
    return _CHAT_TOOL_CONTEXT.set(state)


def reset_chat_tool_context(token: Token) -> None:
    try:
        _CHAT_TOOL_CONTEXT.reset(token)
    except Exception:  # pragma: no cover — token from another context
        _CHAT_TOOL_CONTEXT.set(None)


def get_chat_tool_state() -> Optional[ChatToolState]:
    return _CHAT_TOOL_CONTEXT.get()


def _project_uuid(state: ChatToolState) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(state.project_id))
    except (TypeError, ValueError):
        return None


async def _run_bounded(tool_name: str, fetcher, tool_input: str = "") -> str:
    """Shared guard rails: context check → budget check → fetch → truncate,
    meter, trace. ``fetcher(state, tool_input)`` returns ``(text, summary)``.
    """
    state = _CHAT_TOOL_CONTEXT.get()
    if state is None or not state.project_id:
        return _NO_CONTEXT_NOTE
    if state.token_budget_remaining <= 0:
        return _BUDGET_EXHAUSTED_NOTE
    # Re-audit M17: every chat tool returns through here, and what it returns
    # goes straight back into the copilot's next prompt. Failure text, suite
    # names and error messages are written by whatever was under test, so an
    # injected instruction arrives through exactly this path.
    # sanitize_tool_output existed for it and had no caller. Applied BEFORE
    # the token budget so secrets and injections are removed from the full
    # text, and the budget is metered on what the model actually sees.
    from app.services.input_sanitizer import sanitize_tool_output

    try:
        text, summary = await fetcher(state, (tool_input or "").strip())
    except Exception as exc:  # noqa: BLE001 — must never raise into the loop
        logger.debug("chat_tool_degraded", tool=tool_name, error=str(exc))
        # Exception text can quote the failing input, so it is sanitized too.
        return sanitize_tool_output(f"{tool_name} unavailable: {str(exc)[:200]}")
    cap = min(state.per_call_token_cap, max(1, state.token_budget_remaining))
    observed: str = truncate_to_token_budget(sanitize_tool_output(text or "No data found."), cap)
    state.token_budget_remaining -= estimate_token_count(observed)
    state.trace.append({"tool": tool_name, "summary": summary})
    return observed


# ── Fetchers (module-level so tests can patch them individually) ─────────────


def _b(build: Any, cap: bool = False) -> str:
    """"build 105", or "build-2027" as it is: "build build-2027" is what the
    model misread (it reported the streak starting one build late)."""
    text = str(build)
    label = text if text.lower().startswith("build") else f"build {text}"
    return label[:1].upper() + label[1:] if cap else label


def _rate(value: Any) -> str:
    """A pass rate as text. A run still in progress has none: ``:.1f`` on
    None raised, and the tool answered "list_recent_runs unavailable"."""
    return f"{value:.1f}%" if value is not None else "n/a"


async def _fetch_recent_runs(state: ChatToolState, _q: str) -> tuple[str, str]:
    from sqlalchemy import select

    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import TestRun

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(
                    TestRun.id, TestRun.build_number, TestRun.branch, TestRun.status,
                    TestRun.total_tests, TestRun.failed_tests, TestRun.pass_rate,
                    TestRun.start_time,
                )
                .where(TestRun.project_id == _project_uuid(state))
                .order_by(TestRun.start_time.desc())
                .limit(10)
            )
        ).all()
    if not rows:
        return "No test runs recorded for this project yet.", "listed recent runs — none found"
    lines = [
        "| Build | Branch | Status | Tests | Failures | Pass Rate | Date |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        ts = r.start_time.strftime("%Y-%m-%d %H:%M") if r.start_time else "?"
        lines.append(
            f"| {r.build_number} | {r.branch or '?'} | {r.status} | {r.total_tests} "
            f"| {r.failed_tests} | {_rate(r.pass_rate)} | {ts} |"
        )
        if getattr(r, "id", None):
            state.add_ref({"type": "test_run", "id": str(r.id), "build": r.build_number})
    latest = rows[0]
    return (
        "\n".join(lines),
        f"listed last {len(rows)} runs — latest {_b(latest.build_number)} "
        f"at {_rate(latest.pass_rate)} pass rate",
    )


async def _fetch_run_failures(state: ChatToolState, build: str) -> tuple[str, str]:
    from sqlalchemy import select

    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import TestCase, TestRun, TestStatus

    async with AsyncSessionLocal() as db:
        run_q = (
            select(TestRun.id, TestRun.build_number)
            .where(TestRun.project_id == _project_uuid(state))
            .order_by(TestRun.start_time.desc())
        )
        if build:
            run_q = run_q.where(TestRun.build_number == build)
        run_row = (await db.execute(run_q.limit(1))).first()
        if run_row is None:
            return (
                f"No run found{f' for build {build!r}' if build else ''} in this project.",
                "looked up run failures — run not found",
            )
        rows = (
            await db.execute(
                select(
                    TestCase.id, TestCase.test_name, TestCase.suite_name, TestCase.status,
                    TestCase.error_message, TestCase.test_fingerprint,
                )
                .where(
                    TestCase.test_run_id == run_row.id,
                    TestCase.status.in_(
                        [TestStatus.FAILED.value, TestStatus.BROKEN.value]
                    ),
                )
                .order_by(TestCase.test_name.asc())
                .limit(25)
            )
        ).all()
    if not rows:
        return (
            f"{_b(run_row.build_number, cap=True)}: no failed or broken tests.",
            f"checked failures in {_b(run_row.build_number)} — none",
        )
    lines = [f"Failing tests in {_b(run_row.build_number)}:"]
    state.add_ref({"type": "test_run", "id": str(run_row.id), "build": run_row.build_number})
    for r in rows:
        if getattr(r, "id", None):
            state.add_ref({
                "type": "test_case", "id": str(r.id), "run_id": str(run_row.id),
                "name": r.test_name,
            })
        err = (r.error_message or "").replace("\n", " ").strip()[:160]
        lines.append(
            f"- **{r.test_name}** (suite: {r.suite_name or '?'}, {r.status})"
            + (f" — {err}" if err else "")
        )
    return (
        "\n".join(lines),
        f"listed {len(rows)} failing tests in {_b(run_row.build_number)}",
    )


async def _fetch_failure_clusters(state: ChatToolState, build: str) -> tuple[str, str]:
    from sqlalchemy import select

    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import FailureCluster, TestRun

    async with AsyncSessionLocal() as db:
        run_q = (
            select(TestRun.id, TestRun.build_number)
            .where(TestRun.project_id == _project_uuid(state))
            .order_by(TestRun.start_time.desc())
        )
        if build:
            run_q = run_q.where(TestRun.build_number == build)
        run_row = (await db.execute(run_q.limit(1))).first()
        if run_row is None:
            return "No run found to fetch clusters for.", "looked up clusters — run not found"
        rows = (
            await db.execute(
                select(
                    FailureCluster.cluster_id, FailureCluster.label,
                    FailureCluster.size, FailureCluster.representative_error,
                    FailureCluster.regression_classification,
                )
                .where(FailureCluster.test_run_id == run_row.id)
                .order_by(FailureCluster.size.desc())
                .limit(10)
            )
        ).all()
    if not rows:
        return (
            f"{_b(run_row.build_number, cap=True)}: no failure clusters recorded.",
            f"checked clusters in {_b(run_row.build_number)} — none",
        )
    lines = [f"Failure clusters in {_b(run_row.build_number)}:"]
    for r in rows:
        err = (r.representative_error or "").replace("\n", " ").strip()[:140]
        klass = f" [{r.regression_classification}]" if r.regression_classification else ""
        lines.append(f"- {r.cluster_id} **{r.label}** ({r.size} tests){klass}" + (f": {err}" if err else ""))
    return (
        "\n".join(lines),
        f"found {len(rows)} failure clusters in {_b(run_row.build_number)}",
    )


_ACTIVE_QUARANTINE_STATES = ("QUARANTINED", "RECHECK_SCHEDULED", "RE_QUARANTINED")


async def _fetch_quarantine_status(state: ChatToolState, test_name: str) -> tuple[str, str]:
    from sqlalchemy import select

    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import (
        AIAnalysis,
        FlakyQuarantineRequest,
        TestCase,
        TestRun,
    )

    project_uuid = _project_uuid(state)
    if not test_name:
        return (
            "Provide a test name to check flaky/quarantine status.",
            "checked quarantine status — no test named",
        )
    async with AsyncSessionLocal() as db:
        q_rows = (
            await db.execute(
                select(
                    FlakyQuarantineRequest.test_name,
                    FlakyQuarantineRequest.test_fingerprint,
                    FlakyQuarantineRequest.suite_name,
                    FlakyQuarantineRequest.status,
                    FlakyQuarantineRequest.flip_rate,
                    FlakyQuarantineRequest.fail_count,
                )
                .where(
                    FlakyQuarantineRequest.project_id == project_uuid,
                    FlakyQuarantineRequest.test_name.ilike(f"%{test_name}%"),
                )
                .order_by(FlakyQuarantineRequest.updated_at.desc())
                .limit(5)
            )
        ).all()
        flaky_rows = (
            await db.execute(
                select(
                    TestCase.test_name, TestCase.test_fingerprint,
                    TestCase.suite_name, AIAnalysis.is_flaky,
                )
                .join(AIAnalysis, AIAnalysis.test_case_id == TestCase.id)
                .join(TestRun, TestRun.id == TestCase.test_run_id)
                .where(
                    TestRun.project_id == project_uuid,
                    TestCase.test_name.ilike(f"%{test_name}%"),
                    AIAnalysis.is_flaky.is_(True),
                )
                .order_by(AIAnalysis.created_at.desc())
                .limit(3)
            )
        ).all()

    lines: list[str] = []
    quarantined_fps = set()
    for r in q_rows:
        lines.append(
            f"- **{r.test_name}**: quarantine state {r.status}"
            + (f", flip rate {r.flip_rate:.0%}" if r.flip_rate is not None else "")
        )
        if r.status in _ACTIVE_QUARANTINE_STATES:
            quarantined_fps.add(r.test_fingerprint)

    for r in flaky_rows:
        if r.test_fingerprint and r.test_fingerprint not in quarantined_fps:
            lines.append(f"- **{r.test_name}**: flagged flaky by AI analysis, NOT quarantined")
            # Finding: flaky but not under active quarantine → handoff candidate.
            state.quarantine_candidates.setdefault(r.test_fingerprint, {
                "test_fingerprint": r.test_fingerprint,
                "test_name": r.test_name,
                "suite_name": r.suite_name,
            })
    # Proposed-but-not-active quarantine rows are also actionable context.
    for r in q_rows:
        if r.status in ("DETECTED",) and r.test_fingerprint not in quarantined_fps:
            state.quarantine_candidates.setdefault(r.test_fingerprint, {
                "test_fingerprint": r.test_fingerprint,
                "test_name": r.test_name,
                "suite_name": r.suite_name,
                "fail_count": r.fail_count,
            })

    if not lines:
        # Say what was checked. "No flaky signal" read as "not flaky" beside the
        # flaky-tests list, which measures run history (testCheckoutCase03,
        # 33% failure rate, homelab 2026-10-10).
        return (
            f"No quarantine record for tests matching {test_name!r}, and no AI analysis "
            "flagged one as flaky. Run-history flakiness is a separate measure: the "
            "flaky-tests list and the test's history report it.",
            f"checked quarantine status for '{test_name[:60]}' — no record",
        )
    n_active = len(quarantined_fps)
    return (
        "\n".join(lines),
        f"checked flaky/quarantine status for '{test_name[:60]}' — "
        f"{len(lines)} record(s), {n_active} actively quarantined",
    )


async def _fetch_release_gate(state: ChatToolState, _q: str) -> tuple[str, str]:
    from sqlalchemy import select

    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import ReleaseDecision, TestRun

    project_uuid = _project_uuid(state)
    async with AsyncSessionLocal() as db:
        latest = (
            await db.execute(
                select(TestRun.id, TestRun.build_number)
                .where(TestRun.project_id == project_uuid)
                .order_by(TestRun.start_time.desc())
                .limit(1)
            )
        ).first()
        # The verdict on the NEWEST RUN that has one -- ordered by the run, not
        # by when the decision row was written. Ordered by created_at, the
        # homelab's E-Commerce Platform answered with build-2000's verdict
        # while build-2029 was the latest run, unlabelled as such.
        row = (
            await db.execute(
                select(
                    ReleaseDecision.recommendation, ReleaseDecision.risk_score,
                    ReleaseDecision.blocking_issues, ReleaseDecision.reasoning,
                    TestRun.id.label("run_id"), TestRun.build_number,
                )
                .join(TestRun, TestRun.id == ReleaseDecision.test_run_id)
                .where(TestRun.project_id == project_uuid)
                .order_by(TestRun.start_time.desc(), ReleaseDecision.created_at.desc())
                .limit(1)
            )
        ).first()
    if row is None:
        return (
            "No release-gate decision recorded for this project yet. Without one, say that the "
            "release gate has not evaluated any build; describe the latest run's failures only "
            "as test results, not as a release verdict.",
            "checked release gate — no decision yet",
        )
    state.add_ref({"type": "test_run", "id": str(row.run_id), "build": row.build_number})
    blockers = row.blocking_issues or []
    lines = []
    if latest is not None and str(latest.id) != str(row.run_id):
        lines.append(
            f"The latest run ({_b(latest.build_number)}) has NO release-gate verdict. "
            f"The newest verdict is for an older run, {_b(row.build_number)}:"
        )
    lines.append(
        f"Release-gate verdict for {_b(row.build_number)}: "
        f"**{row.recommendation}** (risk score {row.risk_score}/100)"
    )
    if row.reasoning:
        lines.append(f"Reasoning: {str(row.reasoning)[:400]}")
    for b in blockers[:5]:
        lines.append(f"- Blocker: {b}")
    return (
        "\n".join(lines),
        f"checked release gate — {row.recommendation} on {_b(row.build_number)}",
    )


async def _fetch_failure_history(state: ChatToolState, test_name: str) -> tuple[str, str]:
    from sqlalchemy import select

    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import TestCase, TestRun, TestStatus
    from app.services.memory_recall import recall_failure_history, render_recall_report

    project_uuid = _project_uuid(state)
    if not test_name:
        return (
            "Provide a test name to recall its failure history.",
            "recalled failure history — no test named",
        )
    async with AsyncSessionLocal() as db:
        match = (
            await db.execute(
                select(TestCase.test_name, TestCase.test_fingerprint, TestCase.suite_name)
                .join(TestRun, TestRun.id == TestCase.test_run_id)
                .where(
                    TestRun.project_id == project_uuid,
                    TestCase.test_name.ilike(f"%{test_name}%"),
                    TestCase.status.in_(
                        [TestStatus.FAILED.value, TestStatus.BROKEN.value]
                    ),
                    TestCase.test_fingerprint.isnot(None),
                )
                .order_by(TestCase.created_at.desc())
                .limit(1)
            )
        ).first()
        if match is None:
            return (
                f"No failing test matching {test_name!r} found in this project.",
                f"recalled history for '{test_name[:60]}' — no match",
            )
        recall = await recall_failure_history(
            db,
            project_uuid,  # type: ignore[arg-type]
            test_fingerprint=match.test_fingerprint,
            error_text=None,
            test_name=match.test_name,
        )
    corrections = len(recall.get("corrections") or [])
    prior = len(recall.get("prior_analyses") or [])
    if recall.get("has_history") and (corrections or prior >= 2):
        # Finding: a repeat offender with real history → Jira handoff candidate.
        state.jira_candidates.setdefault(match.test_fingerprint, {
            "fingerprint": match.test_fingerprint,
            "test_name": match.test_name,
            "suite_name": match.suite_name,
        })
    summary = (
        f"checked failure history for '{match.test_name[:60]}' — "
        f"{corrections} prior correction(s), {prior} prior analysis(es)"
        if recall.get("has_history")
        else f"checked failure history for '{match.test_name[:60]}' — no prior history"
    )
    return render_recall_report(recall), summary


async def _fetch_failure_kind_counts(state: ChatToolState, _q: str) -> tuple[str, str]:
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import AIAnalysis, TestCase, TestRun
    from app.services.failure_kind import kind_counts

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(
                    AIAnalysis.failure_category,
                    TestCase.status,
                    func.count(AIAnalysis.id),
                )
                .join(TestCase, TestCase.id == AIAnalysis.test_case_id)
                .join(TestRun, TestRun.id == TestCase.test_run_id)
                .where(
                    TestRun.project_id == _project_uuid(state),
                    AIAnalysis.created_at >= cutoff,
                )
                .group_by(AIAnalysis.failure_category, TestCase.status)
            )
        ).all()
    counts = kind_counts([(r[0], r[1], r[2]) for r in rows])
    total = sum(c["count"] for c in counts)
    if total == 0:
        return (
            "No analyzed failures in the last 7 days.",
            "counted failure kinds (7d) — none",
        )
    lines = ["Failure-kind counts, last 7 days:"]
    lines += [f"- {c['kind']}: {c['count']}" for c in counts if c["count"] > 0]
    top = max(counts, key=lambda c: c["count"])
    return (
        "\n".join(lines),
        f"counted failure kinds (7d) — {total} total, mostly {top['kind']}",
    )


_FAILING_STATUSES = ("FAILED", "BROKEN")
# Runs shown by get_test_history.
_HISTORY_RUNS = 20


def _status_word(status: Any) -> str:
    return str(getattr(status, "value", status) or "UNKNOWN").upper()


def _outcome(status: str) -> str:
    if status in _FAILING_STATUSES:
        return "fail"
    return "pass" if status == "PASSED" else "other"


def _like(text: str) -> str:
    """An ILIKE pattern that matches ``text`` literally (``%``/``_`` escaped)."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def _resolve_test(db: Any, project_uuid: Any, test_name: str) -> tuple[Optional[Any], list[str]]:
    """The named test in this project — exact name first, then a unique
    partial match. Returns ``(row, [])`` or ``(None, candidate names)``."""
    from sqlalchemy import select

    from app.models.postgres import TestCase, TestRun

    base = (
        select(TestCase.test_name, TestCase.test_fingerprint, TestCase.suite_name)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(TestRun.project_id == project_uuid, TestCase.test_fingerprint.isnot(None))
        .order_by(TestRun.start_time.desc())
    )
    exact = (await db.execute(base.where(TestCase.test_name == test_name).limit(1))).first()
    if exact is not None:
        return exact, []
    rows = (
        await db.execute(base.where(TestCase.test_name.ilike(_like(test_name), escape="\\")).limit(200))
    ).all()
    by_name: dict[str, Any] = {}
    for row in rows:
        by_name.setdefault(row.test_name, row)
    if len(by_name) == 1:
        return next(iter(by_name.values())), []
    return None, list(by_name)[:8]


async def _fetch_test_history(state: ChatToolState, test_name: str) -> tuple[str, str]:
    from sqlalchemy import select

    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import TestCase, TestRun
    from app.services.test_case_history_service import _flakiness_in_window

    project_uuid = _project_uuid(state)
    if project_uuid is None:
        return _NO_CONTEXT_NOTE, "looked up test history — no project"
    if not test_name:
        return "Provide a test name to look up its history.", "looked up test history — no test named"
    async with AsyncSessionLocal() as db:
        match, candidates = await _resolve_test(db, project_uuid, test_name)
        if match is None:
            if candidates:
                return (
                    f"Several tests match {test_name!r}: " + ", ".join(candidates)
                    + ". Ask which one, or call again with the exact name.",
                    f"looked up history for '{test_name[:60]}' — {len(candidates)} candidates",
                )
            return (
                f"No test matching {test_name!r} has run in this project.",
                f"looked up history for '{test_name[:60]}' — no match",
            )
        rows = (
            await db.execute(
                select(
                    TestRun.id.label("run_id"), TestRun.build_number, TestRun.start_time,
                    TestCase.id.label("case_id"), TestCase.status, TestCase.error_message,
                )
                .join(TestCase, TestCase.test_run_id == TestRun.id)
                .where(
                    TestRun.project_id == project_uuid,
                    TestCase.test_fingerprint == match.test_fingerprint,
                )
                .order_by(TestRun.start_time.desc())
                .limit(_HISTORY_RUNS)
            )
        ).all()
        window = await _flakiness_in_window(db, project_uuid, match.test_fingerprint)

    if not rows:
        return f"No runs recorded for {match.test_name}.", f"looked up history for '{match.test_name[:60]}' — none"
    statuses = [_status_word(r.status) for r in rows]  # newest first
    outcomes = [_outcome(s) for s in statuses]
    streak = 1
    while streak < len(outcomes) and outcomes[streak] == outcomes[0]:
        streak += 1
    fails = outcomes.count("fail")
    passes = outcomes.count("pass")
    decided = [o for o in outcomes if o != "other"]
    flips = sum(1 for a, b in zip(decided, decided[1:]) if a != b)

    lines = [
        f"History of **{match.test_name}** (suite: {match.suite_name or '?'}) in the "
        f"{len(rows)} most recent runs of this project that ran it, newest first:",
        "| Build | Date | Status | Error |",
        "|---|---|---|---|",
    ]
    for r, status in zip(rows, statuses):
        when = r.start_time.strftime("%Y-%m-%d %H:%M") if r.start_time else "?"
        err = ""
        if status in _FAILING_STATUSES:
            err = (r.error_message or "").replace("\n", " ").replace("|", "/").strip()[:120]
        lines.append(f"| {r.build_number} | {when} | {status} | {err} |")
        state.add_ref({"type": "test_run", "id": str(r.run_id), "build": r.build_number})
    newest = rows[0]
    state.add_ref({
        "type": "test_case", "id": str(newest.case_id), "run_id": str(newest.run_id),
        "name": match.test_name,
    })

    current = statuses[0]
    if outcomes[0] == "fail":
        since = rows[streak - 1].build_number
        streak_builds = ", ".join(str(r.build_number) for r in reversed(rows[:streak]))
        last_pass = next((r for r, o in zip(rows, outcomes) if o == "pass"), None)
        summary = (
            f"Currently {current}: failing in the last {streak} run(s) in a row ({streak_builds}). "
            f"The first failure of this streak was {_b(since)}"
            + (f"; it last passed in {_b(last_pass.build_number)}." if last_pass else
               "; it has not passed in any of these runs.")
        )
    elif outcomes[0] == "pass":
        last_fail = next((r for r, o in zip(rows, outcomes) if o == "fail"), None)
        summary = (
            f"Currently PASSED: passing in the last {streak} run(s) in a row"
            + (f"; it last failed in {_b(last_fail.build_number)}." if last_fail else
               "; no failure in any of these runs.")
        )
    else:
        summary = f"Currently {current}."
    lines.append("")
    lines.append(summary)
    lines.append(
        f"In these {len(rows)} runs: {passes} passed, {fails} failed or broken, "
        f"{len(rows) - passes - fails} other; {flips} change(s) between pass and fail."
    )
    lines.append("Pattern: " + _history_pattern(rows, outcomes, streak, flips))
    total_30 = int(window.get("total_runs") or 0)
    if total_30:
        lines.append(
            f"Last {window.get('window_days', 30)} days: {total_30} runs, "
            f"{window.get('failed', 0)} failed ({window.get('failure_rate_pct', 0)}%)."
        )
    else:
        lines.append("No runs of this test in the last 30 days.")
    return (
        "\n".join(lines),
        f"checked the history of '{match.test_name[:60]}' — {current.lower()}, "
        f"{fails} of {len(rows)} recent runs failed",
    )


def _history_pattern(rows: list, outcomes: list[str], streak: int, flips: int) -> str:
    """Name the pattern from the statuses, so the model does not have to.

    Measured 2026-10-09: given a test failing the last 3 runs after 8 passes,
    the model called it "intermittent, which suggests it is flaky". The rule
    is the platform's own (flaky = oscillation, not regression; see
    analytics_service.flaky_tests).
    """
    decided = [o for o in outcomes if o != "other"]
    if outcomes and outcomes[0] == "fail" and streak >= 2:
        before = outcomes[streak:]
        passes_before = 0
        for o in before:
            if o != "pass":
                break
            passes_before += 1
        if passes_before >= 2:
            return (
                f"a REGRESSION — it has failed every run since {_b(rows[streak - 1].build_number)} "
                f"after passing the {passes_before} runs before it."
            )
        if not before:
            return "failing in every run shown; there is no passing run to compare with."
    if flips >= 3:
        return (
            f"INTERMITTENT — {flips} changes between pass and fail in {len(decided)} runs, "
            "which is what flakiness looks like."
        )
    if outcomes and outcomes[0] == "fail" and streak == 1:
        return "failed only in the latest run so far; too early to tell a regression from a one-off."
    if decided.count("fail") == 1:
        return "one failure in these runs; otherwise passing."
    if "fail" not in decided:
        return "stable — no failure in these runs."
    return "mixed; no clear regression or flakiness pattern in these runs."


def _build_tokens(text: str) -> list[str]:
    """Build numbers named in free text: "104 vs 105", "build 104 and 105"."""
    import re

    # Filler words only -- never the "build" of a build number like
    # "build-2029" (the homelab's E-Commerce builds are named that way).
    cleaned = re.sub(
        r"\b(builds?|runs?|compare|between|with|from)\b(?![-_.\d])", " ", text or "", flags=re.IGNORECASE,
    )
    parts = re.split(r"\s*(?:,|;|\bvs\.?|\bversus\b|\band\b|\bto\b|->|→|\s)\s*", cleaned, flags=re.IGNORECASE)
    return [p.strip().lstrip("#") for p in parts if p and p.strip().lstrip("#")]


async def _fetch_build_comparison(state: ChatToolState, builds: str) -> tuple[str, str]:
    from sqlalchemy import select

    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import TestRun
    from app.services.run_compare_service import compare_runs

    project_uuid = _project_uuid(state)
    tokens = _build_tokens(builds)
    cols = (TestRun.id, TestRun.build_number, TestRun.start_time, TestRun.primary_suite_name)
    older: Any = None
    newer: Any = None
    async with AsyncSessionLocal() as db:
        async def by_build(build: str) -> Optional[Any]:
            return (
                await db.execute(
                    select(*cols)
                    .where(TestRun.project_id == project_uuid, TestRun.build_number == build)
                    .order_by(TestRun.start_time.desc())
                    .limit(1)
                )
            ).first()

        async def previous(run: Any) -> Optional[Any]:
            q = (
                select(*cols)
                .where(
                    TestRun.project_id == project_uuid,
                    TestRun.start_time < run.start_time,
                    TestRun.id != run.id,
                )
                .order_by(TestRun.start_time.desc())
            )
            # The run's own suite, and "no primary suite" is one too: the
            # homelab's build-2029 (none) was compared with viz-3044, a
            # CheckoutSuite run on a feature branch, instead of build-2028.
            same = (
                TestRun.primary_suite_name == run.primary_suite_name
                if run.primary_suite_name else TestRun.primary_suite_name.is_(None)
            )
            same_suite = (await db.execute(q.where(same).limit(1))).first()
            if same_suite is not None:
                return same_suite
            return (await db.execute(q.limit(1))).first()

        if len(tokens) >= 2:
            first, second = await by_build(tokens[0]), await by_build(tokens[1])
            missing = [t for t, r in ((tokens[0], first), (tokens[1], second)) if r is None]
            if missing:
                return (
                    f"No run with build {', '.join(repr(m) for m in missing)} in this project.",
                    "compared builds — build not found",
                )
            older, newer = sorted((first, second), key=lambda r: getattr(r, "start_time", None) or _datetime_min())
        else:
            if tokens:
                newer = await by_build(tokens[0])
                if newer is None:
                    return f"No run with build {tokens[0]!r} in this project.", "compared builds — build not found"
            else:
                newer = (
                    await db.execute(
                        select(*cols).where(TestRun.project_id == project_uuid)
                        .order_by(TestRun.start_time.desc()).limit(1)
                    )
                ).first()
                if newer is None:
                    return "No test runs recorded for this project yet.", "compared builds — no runs"
            older = await previous(newer) if newer.start_time is not None else None
            if older is None:
                return (
                    f"{_b(newer.build_number, cap=True)} has no earlier run in this project to compare with.",
                    f"compared builds — nothing before {newer.build_number}",
                )
        doc = await compare_runs(db, older.id, newer.id)

    for run in (older, newer):
        state.add_ref({"type": "test_run", "id": str(run.id), "build": run.build_number})
    left, right = doc.get("left") or {}, doc.get("right") or {}

    def rate(summary: dict) -> str:
        return _rate(summary.get("pass_rate"))

    lines = [
        f"{_b(older.build_number, cap=True)} → {_b(newer.build_number)}"
        + (f" (suite {newer.primary_suite_name})" if newer.primary_suite_name else "") + ":",
        f"- Pass rate {rate(left)} → {rate(right)}; failed {left.get('failed_tests', '?')} → "
        f"{right.get('failed_tests', '?')}; broken {left.get('broken_tests', '?')} → "
        f"{right.get('broken_tests', '?')}; tests {left.get('total_tests', '?')} → {right.get('total_tests', '?')}.",
        f"- {doc.get('new_failures', 0)} new failure(s), {doc.get('fixed', 0)} fixed, "
        f"{doc.get('still_failing', 0)} still failing, {doc.get('new_tests', 0)} new test(s), "
        f"{doc.get('removed_tests', 0)} removed.",
    ]
    deltas = doc.get("test_deltas") or []
    for label, klass in (("New failures", "new_failure"), ("Fixed", "fixed"), ("Still failing", "still_failing")):
        names = [d for d in deltas if d.get("classification") == klass]
        if not names:
            continue
        lines.append(f"{label} ({len(names)}):")
        for d in names[:15]:
            lines.append(
                f"- **{d.get('test_name') or '(unnamed)'}** (suite: {d.get('suite_name') or '?'}, "
                f"{d.get('left_status') or 'absent'} → {d.get('right_status') or 'absent'})"
            )
        if len(names) > 15:
            lines.append(f"- … and {len(names) - 15} more")
    return (
        "\n".join(lines),
        f"compared {_b(older.build_number)} with {_b(newer.build_number)} — "
        f"{doc.get('new_failures', 0)} new failure(s), {doc.get('fixed', 0)} fixed",
    )


def _datetime_min() -> Any:
    from datetime import datetime, timezone

    return datetime.min.replace(tzinfo=timezone.utc)


async def _fetch_flaky_tests(state: ChatToolState, _q: str) -> tuple[str, str]:
    from app.db.postgres import AsyncSessionLocal
    from app.services.analytics_service import flaky_tests

    from sqlalchemy import select

    from app.models.postgres import FlakyQuarantineRequest

    project_uuid = _project_uuid(state)
    async with AsyncSessionLocal() as db:
        data = await flaky_tests(db, str(project_uuid), days=30, limit=15)
        items = data.get("items") or []
        fingerprints = [it.get("test_fingerprint") for it in items if it.get("test_fingerprint")]
        quarantine: dict[str, str] = {}
        if fingerprints:
            # Latest workflow state per test. Asked "which are flaky, are any
            # quarantined?", the model listed the flaky tests, wrote "now
            # let's check if any are quarantined" and stopped -- so the answer
            # to the usual follow-up is in the same result.
            q_rows = (
                await db.execute(
                    select(FlakyQuarantineRequest.test_fingerprint, FlakyQuarantineRequest.status)
                    .where(
                        FlakyQuarantineRequest.project_id == project_uuid,
                        FlakyQuarantineRequest.test_fingerprint.in_(fingerprints),
                    )
                    .order_by(FlakyQuarantineRequest.updated_at.asc())
                )
            ).all()
            for fp, status in q_rows:
                quarantine[fp] = str(getattr(status, "value", status))
    total = data.get("total", len(items))
    if not items:
        return (
            "No flaky tests in the last 30 days: no test both passed and failed in that window, "
            "and none was marked flaky by a person.",
            "listed flaky tests (30d) — none",
        )
    lines = [
        f"Flaky tests in the last 30 days ({len(items)} shown of {total}), the same list as the "
        "Flaky tests page. Auto = the test both passed and failed (status flips); manual = a "
        "person marked it flaky.",
        "| Test | Suite | Runs | Failed | Passed | Failure rate | Source | Quarantine |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for it in items:
        lines.append(
            f"| {it.get('test_name')} | {it.get('suite_name') or '?'} | {it.get('total_runs')} "
            f"| {it.get('fail_count')} | {it.get('pass_count')} | {it.get('failure_rate_pct')}% "
            f"| {it.get('source', 'auto')} | {quarantine.get(it.get('test_fingerprint'), 'none')} |"
        )
    n_quarantined = sum(1 for s in quarantine.values() if s in _ACTIVE_QUARANTINE_STATES)
    lines.append(
        f"Quarantine column: the latest quarantine workflow state; 'none' means no quarantine record. "
        f"{n_quarantined} of the tests shown are actively quarantined."
    )
    return "\n".join(lines), f"listed flaky tests (30d) — {total} found, {n_quarantined} quarantined"


# Status line shown while a tool runs ("Checking the history of test_x…").
_STATUS_LABELS: dict[str, tuple[str, str]] = {
    # name: (label with the argument, label without one)
    "list_recent_runs": ("Listing recent runs…", "Listing recent runs…"),
    "list_run_failures": ("Reading the failures in {arg}…", "Reading the latest run's failures…"),
    "get_failure_clusters": ("Grouping the failures in {arg}…", "Grouping the latest run's failures…"),
    "check_quarantine_status": ("Checking quarantine status of {arg}…", "Checking quarantine status…"),
    "get_release_gate_verdict": ("Reading the release-gate verdict…", "Reading the release-gate verdict…"),
    "recall_failure_history": ("Recalling what is known about {arg}…", "Recalling earlier analyses…"),
    "count_failure_kinds": ("Counting failure kinds…", "Counting failure kinds…"),
    "get_test_history": ("Checking the history of {arg}…", "Checking a test's history…"),
    "compare_builds": ("Comparing builds {arg}…", "Comparing the latest build with the previous one…"),
    "list_flaky_tests": ("Listing flaky tests…", "Listing flaky tests…"),
}


def tool_status_label(name: str, args: Any) -> str:
    """What the chat shows while ``name`` runs, naming its argument."""
    with_arg, without = _STATUS_LABELS.get(name, (f"Running {name}…", f"Running {name}…"))
    arg = ""
    if isinstance(args, dict):
        arg = next((str(v) for v in args.values() if isinstance(v, str) and v.strip()), "")
    elif isinstance(args, str):
        arg = args
    arg = arg.strip()[:60]
    if arg and name in ("list_run_failures", "get_failure_clusters"):
        arg = _b(arg)
    return with_arg.format(arg=arg) if arg else without


# ── LangChain tools ──────────────────────────────────────────────────────────


@tool
async def list_recent_runs(query: str = "") -> str:
    """List the project's 10 most recent test runs: build number, branch,
    status, test counts, failure counts, pass rate, and date. Use this first
    for questions about trends, latest results, or which build to dig into.

    Args:
        query: Ignored free text. The project scope comes from the
            conversation context, not from this input.
    """
    return await _run_bounded("list_recent_runs", _fetch_recent_runs, query)


@tool
async def list_run_failures(build_number: str = "") -> str:
    """List the failed and broken tests of one run (name, suite, status,
    error excerpt). Defaults to the most recent run when no build number is
    given.

    Args:
        build_number: Optional exact build number; latest run when empty.
    """
    return await _run_bounded("list_run_failures", _fetch_run_failures, build_number)


@tool
async def get_failure_clusters(build_number: str = "") -> str:
    """List the failure clusters of one run — groups of failures sharing a
    root-cause signature, with label, size, and representative error.
    Defaults to the most recent run.

    Args:
        build_number: Optional exact build number; latest run when empty.
    """
    return await _run_bounded("get_failure_clusters", _fetch_failure_clusters, build_number)


@tool
async def check_quarantine_status(test_name: str) -> str:
    """Check whether a test is flaky and/or quarantined: quarantine manifest
    state (proposed / quarantined / released), measured flip rate, and any
    AI flaky flags. Use before recommending quarantine or dismissing a
    failure as flaky.

    Args:
        test_name: Full or partial test name to look up.
    """
    return await _run_bounded("check_quarantine_status", _fetch_quarantine_status, test_name)


@tool
async def get_release_gate_verdict(query: str = "") -> str:
    """Get the latest release-gate decision for the project: GO /
    CONDITIONAL_GO / NO_GO recommendation, risk score, blocking issues, and
    reasoning.

    Args:
        query: Ignored free text.
    """
    return await _run_bounded("get_release_gate_verdict", _fetch_release_gate, query)


@tool
async def recall_failure_history(test_name: str) -> str:
    """Recall what the platform already knows about a failing test: prior
    human corrections (authoritative), earlier AI root-cause analyses,
    similar past failures, and quarantine/flip history. Use for "have we
    seen this before?" questions.

    Args:
        test_name: Full or partial test name to look up.
    """
    return await _run_bounded("recall_failure_history", _fetch_failure_history, test_name)


@tool
async def count_failure_kinds(query: str = "") -> str:
    """Count the project's analyzed failures of the last 7 days by kind
    (product_bug / infrastructure / test_code / unknown). Use for "what is
    mostly failing" style questions.

    Args:
        query: Ignored free text.
    """
    return await _run_bounded("count_failure_kinds", _fetch_failure_kind_counts, query)


@tool
async def get_test_history(test_name: str) -> str:
    """Show one test's status (PASSED / FAILED / BROKEN / SKIPPED) in each of
    the project's recent runs that ran it, newest first, with build numbers,
    dates and error excerpts; plus the current streak, when it started failing,
    when it last passed, and how often it changed between pass and fail. Use
    for "is X flaky or a regression?", "when did X start failing?", "how often
    does X fail?".

    Args:
        test_name: Full or partial test name.
    """
    return await _run_bounded("get_test_history", _fetch_test_history, test_name)


@tool
async def compare_builds(builds: str = "") -> str:
    """Compare two runs: pass-rate and count changes, and the names of new
    failures, fixed tests and tests still failing. Empty compares the latest
    run with the previous run of the same suite; one build number compares
    that build with its previous run; two build numbers ("104 vs 105")
    compare those two. Use for "what changed / broke since the last build?".

    Args:
        builds: "", "<build>", or "<build> vs <build>".
    """
    return await _run_bounded("compare_builds", _fetch_build_comparison, builds)


@tool
async def list_flaky_tests(query: str = "") -> str:
    """List the project's flaky tests of the last 30 days (tests that both
    passed and failed, or that a person marked flaky), with run counts,
    failure rates and each test's quarantine state — the same list as the
    Flaky tests page. Use for "which tests are flaky?" and "are any
    quarantined?"; call it alone and answer from its list.

    Args:
        query: Ignored free text.
    """
    return await _run_bounded("list_flaky_tests", _fetch_flaky_tests, query)


def chat_tools() -> list:
    """The chat's read-only toolset."""
    return [
        list_recent_runs,
        list_run_failures,
        get_test_history,
        compare_builds,
        list_flaky_tests,
        check_quarantine_status,
        get_failure_clusters,
        get_release_gate_verdict,
        recall_failure_history,
        count_failure_kinds,
    ]


def build_suggested_actions(state: ChatToolState) -> list[dict[str, Any]]:
    """Deterministic action handoffs from structured tool findings.

    Never derived from LLM text — only from what the read tools actually
    observed. The human clicks and submits through the existing audited
    flows (US-2.4 quarantine proposal, US-6.1 Jira dialog); the agent never
    submits anything.
    """
    actions: list[dict[str, Any]] = []
    for cand in state.quarantine_candidates.values():
        actions.append({
            "type": "propose_quarantine",
            "label": f"Propose quarantine: {cand['test_name']}",
            "prefill": {
                "project_id": state.project_id,
                "test_fingerprint": cand["test_fingerprint"],
                "test_name": cand["test_name"],
                "suite_name": cand.get("suite_name"),
                **({"fail_count": cand["fail_count"]} if cand.get("fail_count") else {}),
            },
        })
    for cand in state.jira_candidates.values():
        actions.append({
            "type": "create_jira",
            "label": f"Create Jira issue: {cand['test_name']}",
            "prefill": {
                "project_id": state.project_id,
                "fingerprint": cand["fingerprint"],
                "test_name": cand["test_name"],
            },
        })
    return actions[:3]
