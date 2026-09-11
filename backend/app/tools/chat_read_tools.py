"""Read-only tools for the Ask-AI chat copilot loop (Agentic plan AI-6).

Seven thin, READ-ONLY wrappers over existing services/queries that the
bounded ReAct chat loop may call. Three invariants are load-bearing:

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

# Max tool calls the chat loop may make (mirrors the ReAct triage cap).
CHAT_TOOL_LOOP_MAX_CALLS = 6

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
    text = truncate_to_token_budget(sanitize_tool_output(text or "No data found."), cap)
    state.token_budget_remaining -= estimate_token_count(text)
    state.trace.append({"tool": tool_name, "summary": summary})
    return text


# ── Fetchers (module-level so tests can patch them individually) ─────────────


async def _fetch_recent_runs(state: ChatToolState, _q: str) -> tuple[str, str]:
    from sqlalchemy import select

    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import TestRun

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(
                    TestRun.build_number, TestRun.branch, TestRun.status,
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
            f"| {r.failed_tests} | {r.pass_rate:.1f}% | {ts} |"
        )
    latest = rows[0]
    return (
        "\n".join(lines),
        f"listed last {len(rows)} runs — latest build {latest.build_number} "
        f"at {latest.pass_rate:.1f}% pass rate",
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
                    TestCase.test_name, TestCase.suite_name, TestCase.status,
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
            f"Build {run_row.build_number}: no failed or broken tests.",
            f"checked failures in build {run_row.build_number} — none",
        )
    lines = [f"Failing tests in build {run_row.build_number}:"]
    for r in rows:
        err = (r.error_message or "").replace("\n", " ").strip()[:160]
        lines.append(
            f"- **{r.test_name}** (suite: {r.suite_name or '?'}, {r.status})"
            + (f" — {err}" if err else "")
        )
    return (
        "\n".join(lines),
        f"listed {len(rows)} failing tests in build {run_row.build_number}",
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
            f"Build {run_row.build_number}: no failure clusters recorded.",
            f"checked clusters in build {run_row.build_number} — none",
        )
    lines = [f"Failure clusters in build {run_row.build_number}:"]
    for r in rows:
        err = (r.representative_error or "").replace("\n", " ").strip()[:140]
        klass = f" [{r.regression_classification}]" if r.regression_classification else ""
        lines.append(f"- {r.cluster_id} **{r.label}** ({r.size} tests){klass}" + (f": {err}" if err else ""))
    return (
        "\n".join(lines),
        f"found {len(rows)} failure clusters in build {run_row.build_number}",
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
        return (
            f"No flaky signal or quarantine record for tests matching {test_name!r}.",
            f"checked flaky/quarantine status for '{test_name[:60]}' — clean",
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

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(
                    ReleaseDecision.recommendation, ReleaseDecision.risk_score,
                    ReleaseDecision.blocking_issues, ReleaseDecision.reasoning,
                    TestRun.build_number,
                )
                .join(TestRun, TestRun.id == ReleaseDecision.test_run_id)
                .where(TestRun.project_id == _project_uuid(state))
                .order_by(ReleaseDecision.created_at.desc())
                .limit(1)
            )
        ).first()
    if row is None:
        return (
            "No release-gate decision recorded for this project yet.",
            "checked release gate — no decision yet",
        )
    blockers = row.blocking_issues or []
    lines = [
        f"Latest release-gate verdict (build {row.build_number}): "
        f"**{row.recommendation}** (risk score {row.risk_score}/100)",
    ]
    if row.reasoning:
        lines.append(f"Reasoning: {str(row.reasoning)[:400]}")
    for b in blockers[:5]:
        lines.append(f"- Blocker: {b}")
    return (
        "\n".join(lines),
        f"checked release gate — {row.recommendation} on build {row.build_number}",
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


def chat_tools() -> list:
    """The chat copilot's read-only toolset (order = prompt listing order)."""
    return [
        list_recent_runs,
        list_run_failures,
        get_failure_clusters,
        check_quarantine_status,
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
