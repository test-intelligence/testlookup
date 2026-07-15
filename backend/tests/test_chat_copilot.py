"""Ask-AI chat copilot — bounded tool loop + trace + action handoffs (AI-6).

Pins the acceptance criteria:
  * the loop is bounded: ≤ 6 tool calls (executor iteration cap), per-call +
    total token budgets on tool observations, hard wall-clock timeout — and
    ANY loop failure falls back to the legacy single-shot path;
  * every read tool is project-scoped by a server-side ContextVar (the LLM
    never supplies identifiers) — no context ⇒ "unavailable", and the SQL
    each fetcher issues binds the CALLER's project id (leakage guard);
  * tool_trace is persisted on the ChatMessage (inside the existing sources
    JSON column — no migration) and returned in the response payload;
  * suggested_actions emit deterministic quarantine / Jira handoffs from
    structured tool findings — never from LLM text;
  * no-LLM / rules mode: behavior is byte-identical to the pre-AI-6 single
    shot path (pinned reply dict, loop never constructed);
  * multi-hop: a scripted fake LLM resolves a two-tool question end-to-end.

All DB access is stubbed — no live services required.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("langchain")

from langchain_core.language_models import FakeListChatModel  # noqa: E402

from app.agents.conversation import ConversationAgent  # noqa: E402
from app.tools import chat_read_tools as crt  # noqa: E402

PROJECT_A = str(uuid.uuid4())
PROJECT_B = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())


# ── Test DB fakes (memory_recall test pattern) ───────────────────────────────


class _FakeResult:
    def __init__(self, rows=None, first=None):
        self._rows = rows or []
        self._first = first

    def all(self):
        return self._rows

    def first(self):
        if self._first is not None:
            return self._first
        return self._rows[0] if self._rows else None


class _FakeDB:
    """Async-context-manager session capturing executed statements."""

    def __init__(self, results=None):
        self._results = list(results or [])
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self._results.pop(0) if self._results else _FakeResult()


def _stmt_params(stmt) -> dict:
    return dict(stmt.compile().params)


def _agent_with_stubbed_persistence(monkeypatch):
    agent = ConversationAgent()
    saved: list[dict] = []

    async def _save(session_id, role, content, sources):
        saved.append({
            "session_id": session_id, "role": role,
            "content": content, "sources": sources,
        })

    monkeypatch.setattr(agent, "_save_message", _save)
    monkeypatch.setattr(agent, "_touch_session", AsyncMock())
    monkeypatch.setattr(agent, "_maybe_compress_history", AsyncMock())
    monkeypatch.setattr(agent, "_load_history", AsyncMock(return_value=([], "")))
    monkeypatch.setattr(agent, "_fetch_project_name", AsyncMock(return_value="Demo"))
    return agent, saved


def _enable_llm_mode(monkeypatch):
    monkeypatch.setattr(
        "app.services.analysis_router.get_analysis_mode", lambda: "llm"
    )


# ── (a) Tenancy: no context ⇒ unavailable; context binds the project ────────


@pytest.mark.asyncio
async def test_tools_refuse_without_bound_context():
    assert crt.get_chat_tool_state() is None
    for t in crt.chat_tools():
        out = await t.ainvoke({next(iter(t.args)): ""})
        assert "no project context" in out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fetcher,tool_input,n_results",
    [
        (crt._fetch_recent_runs, "", 1),
        (crt._fetch_run_failures, "", 1),
        (crt._fetch_failure_clusters, "", 1),
        (crt._fetch_quarantine_status, "test_checkout", 2),
        (crt._fetch_release_gate, "", 1),
        (crt._fetch_failure_kind_counts, "", 1),
    ],
)
async def test_every_fetcher_scopes_sql_to_bound_project(fetcher, tool_input, n_results):
    """Leakage guard: the first statement each fetcher issues binds the
    ContextVar project id — project B never appears."""
    db = _FakeDB(results=[_FakeResult() for _ in range(n_results)])
    token = crt.set_chat_tool_context(project_id=PROJECT_A)
    try:
        state = crt.get_chat_tool_state()
        with patch("app.db.postgres.AsyncSessionLocal", return_value=db):
            await fetcher(state, tool_input)
    finally:
        crt.reset_chat_tool_context(token)

    assert db.statements, "fetcher issued no SQL"
    params = _stmt_params(db.statements[0])
    bound = {str(v) for v in params.values()}
    assert PROJECT_A in bound
    assert PROJECT_B not in bound


@pytest.mark.asyncio
async def test_failure_history_fetcher_scopes_recall_to_bound_project():
    match = SimpleNamespace(
        test_name="test_checkout", test_fingerprint="fp-1", suite_name="suite",
    )
    db = _FakeDB(results=[_FakeResult(first=match)])
    recall_mock = AsyncMock(return_value={"has_history": False})
    token = crt.set_chat_tool_context(project_id=PROJECT_A)
    try:
        state = crt.get_chat_tool_state()
        with patch("app.db.postgres.AsyncSessionLocal", return_value=db), patch(
            "app.services.memory_recall.recall_failure_history", recall_mock,
        ), patch(
            "app.services.memory_recall.render_recall_report", return_value="none",
        ):
            await crt._fetch_failure_history(state, "test_checkout")
    finally:
        crt.reset_chat_tool_context(token)
    # recall got the server-bound project id, never an LLM-supplied one
    assert str(recall_mock.call_args.args[1]) == PROJECT_A


# ── (b) Budgets: per-call truncation + total exhaustion ─────────────────────


@pytest.mark.asyncio
async def test_token_budget_truncates_and_exhausts():
    token = crt.set_chat_tool_context(
        project_id=PROJECT_A, total_token_budget=40, per_call_token_cap=30,
    )
    try:
        async def _big_fetch(state, q):
            return "word " * 5_000, "fetched a lot"

        out1 = await crt._run_bounded("list_recent_runs", _big_fetch)
        # Truncated well below the raw 25k chars (30-token cap ≈ 120 chars)
        assert len(out1) < 400
        state = crt.get_chat_tool_state()
        assert state is not None and state.token_budget_remaining <= 10

        out2 = await crt._run_bounded("list_recent_runs", _big_fetch)
        out3 = await crt._run_bounded("list_recent_runs", _big_fetch)
        assert "budget exhausted" in out3.lower()
        # The exhausted call records no trace entry (nothing was checked)
        assert len(state.trace) == 2, (out2, state.trace)
    finally:
        crt.reset_chat_tool_context(token)


@pytest.mark.asyncio
async def test_tool_never_raises_into_the_loop():
    token = crt.set_chat_tool_context(project_id=PROJECT_A)
    try:
        async def _boom(state, q):
            raise RuntimeError("db exploded")

        out = await crt._run_bounded("list_recent_runs", _boom)
        assert "unavailable" in out
        state = crt.get_chat_tool_state()
        assert state is not None and state.trace == []
    finally:
        crt.reset_chat_tool_context(token)


# ── (c) Loop bounds: iteration cap and timeout both fall back ───────────────


def _scripted_llm(responses):
    return FakeListChatModel(responses=responses)


@pytest.mark.asyncio
async def test_loop_iteration_cap_then_fallback(monkeypatch):
    """An LLM that never stops acting is cut off at ≤6 tool calls, and the
    unusable loop result falls back to the single-shot path."""
    _enable_llm_mode(monkeypatch)
    agent, saved = _agent_with_stubbed_persistence(monkeypatch)

    calls = {"n": 0}

    async def _counting_fetch(state, q):
        calls["n"] += 1
        return "some runs", "listed runs"

    monkeypatch.setattr(crt, "_fetch_recent_runs", _counting_fetch)

    looping = _scripted_llm(
        ["Thought: dig\nAction: list_recent_runs\nAction Input: more"] * 20
    )
    # Loop LLM is scripted; the single-shot fallback's LLM raises → canned msg
    llm_calls = {"n": 0}

    async def _get_llm(*a, **k):
        llm_calls["n"] += 1
        if llm_calls["n"] == 1:
            return looping
        raise RuntimeError("no fallback LLM in test")

    monkeypatch.setattr("app.agents.conversation.get_llm", _get_llm)
    monkeypatch.setattr(
        agent, "_retrieve_context", AsyncMock(return_value=("ctx", [])),
    )

    result = await agent.chat(SESSION_ID, "what changed?", USER_ID, PROJECT_A)

    assert calls["n"] <= 6
    assert result["tool_trace"] == []          # loop produced no usable answer
    assert result["suggested_actions"] == []
    assert "trouble connecting" in result["reply"]  # single-shot fallback ran
    assert saved[-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_loop_timeout_falls_back_to_single_shot(monkeypatch):
    _enable_llm_mode(monkeypatch)
    agent, _saved = _agent_with_stubbed_persistence(monkeypatch)
    monkeypatch.setattr("app.agents.conversation.settings.AI_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr("app.agents.conversation._TOOL_LOOP_TIMEOUT_GRACE_SECONDS", 0)

    class _HangingExecutor:
        def __init__(self, **kwargs):
            pass

        async def ainvoke(self, _inputs):
            await asyncio.sleep(30)

    import langchain.agents as la
    monkeypatch.setattr(la, "AgentExecutor", _HangingExecutor)
    monkeypatch.setattr(la, "create_react_agent", lambda **k: object())

    # First call feeds the (patched-away) loop; the single-shot fallback's
    # call raises so the canned message marks that the fallback ran.
    llm_calls = {"n": 0}

    async def _get_llm(*a, **k):
        llm_calls["n"] += 1
        if llm_calls["n"] == 1:
            return SimpleNamespace()
        raise RuntimeError("single-shot has no LLM either")

    monkeypatch.setattr("app.agents.conversation.get_llm", _get_llm)
    monkeypatch.setattr(
        agent, "_retrieve_context", AsyncMock(return_value=("ctx", [])),
    )

    result = await agent.chat(SESSION_ID, "slow question", USER_ID, PROJECT_A)
    assert "trouble connecting" in result["reply"]
    assert result["tool_trace"] == []


# ── (d) Multi-hop: two scripted tool calls answer the question ───────────────


@pytest.mark.asyncio
async def test_multi_hop_question_resolved_via_two_tools(monkeypatch):
    """"Which teams own this week's new failures and are any quarantined?"
    — resolved with list_run_failures + check_quarantine_status."""
    _enable_llm_mode(monkeypatch)
    agent, saved = _agent_with_stubbed_persistence(monkeypatch)

    async def _failures(state, build):
        return (
            "Failing tests in build 512:\n"
            "- **test_checkout_flow** (suite: payments-team, FAILED)",
            "listed 1 failing tests in build 512",
        )

    async def _quarantine(state, name):
        state.quarantine_candidates["fp-checkout"] = {
            "test_fingerprint": "fp-checkout",
            "test_name": "test_checkout_flow",
            "suite_name": "payments-team",
        }
        return (
            "- **test_checkout_flow**: flagged flaky by AI analysis, NOT quarantined",
            "checked flaky/quarantine status for 'test_checkout_flow' — 1 record(s), 0 actively quarantined",
        )

    monkeypatch.setattr(crt, "_fetch_run_failures", _failures)
    monkeypatch.setattr(crt, "_fetch_quarantine_status", _quarantine)

    scripted = _scripted_llm([
        "Thought: find this week's failures\nAction: list_run_failures\nAction Input: ",
        "Thought: check quarantine\nAction: check_quarantine_status\nAction Input: test_checkout_flow",
        "Thought: I now have enough information to answer\n"
        "Final Answer: This week's new failure is **test_checkout_flow** owned by the "
        "payments-team suite; it is flagged flaky but NOT quarantined yet.",
    ])
    monkeypatch.setattr(
        "app.agents.conversation.get_llm", AsyncMock(return_value=scripted),
    )

    result = await agent.chat(
        SESSION_ID,
        "which teams own this week's new failures and are any quarantined?",
        USER_ID,
        PROJECT_A,
    )

    assert "payments-team" in result["reply"]
    assert [t["tool"] for t in result["tool_trace"]] == [
        "list_run_failures", "check_quarantine_status",
    ]
    # Transparency phrasing carried through
    assert "checked flaky/quarantine status" in result["tool_trace"][1]["summary"]
    # Flaky-but-not-quarantined finding became a one-click handoff
    assert result["suggested_actions"] == [{
        "type": "propose_quarantine",
        "label": "Propose quarantine: test_checkout_flow",
        "prefill": {
            "project_id": PROJECT_A,
            "test_fingerprint": "fp-checkout",
            "test_name": "test_checkout_flow",
            "suite_name": "payments-team",
        },
    }]
    # Persisted on the ChatMessage inside the sources JSON (no migration)
    persisted = saved[-1]
    assert persisted["role"] == "assistant"
    carrier_types = [s["type"] for s in persisted["sources"]]
    assert "tool_trace" in carrier_types and "suggested_actions" in carrier_types
    trace_entry = next(s for s in persisted["sources"] if s["type"] == "tool_trace")
    assert trace_entry["trace"] == result["tool_trace"]
    # Plain source chips mirror the consulted tools
    assert {"type": "tool", "id": "list_run_failures"} in result["sources"]


# ── (e) Suggested actions are deterministic over structured findings ────────


def test_build_suggested_actions_shapes():
    state = crt.ChatToolState(
        project_id=PROJECT_A, token_budget_remaining=100, per_call_token_cap=50,
    )
    state.quarantine_candidates["fp-1"] = {
        "test_fingerprint": "fp-1", "test_name": "test_a",
        "suite_name": "s", "fail_count": 4,
    }
    state.jira_candidates["fp-2"] = {
        "fingerprint": "fp-2", "test_name": "test_b", "suite_name": None,
    }
    actions = crt.build_suggested_actions(state)
    assert actions == [
        {
            "type": "propose_quarantine",
            "label": "Propose quarantine: test_a",
            "prefill": {
                "project_id": PROJECT_A, "test_fingerprint": "fp-1",
                "test_name": "test_a", "suite_name": "s", "fail_count": 4,
            },
        },
        {
            "type": "create_jira",
            "label": "Create Jira issue: test_b",
            "prefill": {
                "project_id": PROJECT_A, "fingerprint": "fp-2",
                "test_name": "test_b",
            },
        },
    ]


def test_build_suggested_actions_capped_at_three():
    state = crt.ChatToolState(
        project_id=PROJECT_A, token_budget_remaining=100, per_call_token_cap=50,
    )
    for i in range(5):
        state.quarantine_candidates[f"fp-{i}"] = {
            "test_fingerprint": f"fp-{i}", "test_name": f"t{i}", "suite_name": None,
        }
    assert len(crt.build_suggested_actions(state)) == 3


@pytest.mark.asyncio
async def test_recurring_failure_yields_jira_candidate():
    match = SimpleNamespace(
        test_name="test_checkout", test_fingerprint="fp-9", suite_name="s",
    )
    db = _FakeDB(results=[_FakeResult(first=match)])
    recall = {
        "has_history": True,
        "corrections": [],
        "prior_analyses": [{"a": 1}, {"a": 2}],
    }
    token = crt.set_chat_tool_context(project_id=PROJECT_A)
    try:
        state = crt.get_chat_tool_state()
        with patch("app.db.postgres.AsyncSessionLocal", return_value=db), patch(
            "app.services.memory_recall.recall_failure_history",
            AsyncMock(return_value=recall),
        ), patch(
            "app.services.memory_recall.render_recall_report",
            return_value="2 prior analyses",
        ):
            _text, summary = await crt._fetch_failure_history(state, "test_checkout")
        assert "2 prior analysis(es)" in summary
        assert "fp-9" in state.jira_candidates
    finally:
        crt.reset_chat_tool_context(token)


# ── (f) No-LLM / rules mode: byte-identical to the pre-AI-6 path ─────────────


@pytest.mark.asyncio
async def test_rules_mode_pins_unchanged_single_shot_behavior(monkeypatch):
    """In rules mode the loop is never constructed and the reply dict is the
    exact legacy single-shot result (canned LLM-unavailable message here)."""
    monkeypatch.setattr(
        "app.services.analysis_router.get_analysis_mode", lambda: "rules"
    )
    agent, saved = _agent_with_stubbed_persistence(monkeypatch)
    monkeypatch.setattr(
        agent, "_retrieve_context", AsyncMock(return_value=("ctx", [])),
    )

    loop_spy = AsyncMock()
    monkeypatch.setattr(agent, "_run_tool_loop", loop_spy)

    async def _no_llm(*a, **k):
        raise RuntimeError("LLM not configured")

    monkeypatch.setattr("app.agents.conversation.get_llm", _no_llm)

    result = await agent.chat(SESSION_ID, "hello", USER_ID, PROJECT_A)

    loop_spy.assert_not_awaited()
    # Byte-identical legacy payload (plus the additive empty AI-6 keys)
    assert result == {
        "reply": "I'm having trouble connecting to the AI provider. Please try again.",
        "sources": [],
        "tool_trace": [],
        "suggested_actions": [],
    }
    # And the persisted assistant message carries no carrier entries
    assert saved[-1]["sources"] == []


@pytest.mark.asyncio
async def test_all_projects_chat_never_engages_loop(monkeypatch):
    """Without a project scope the loop must not run (tools are tenant-scoped)."""
    _enable_llm_mode(monkeypatch)
    agent, _saved = _agent_with_stubbed_persistence(monkeypatch)
    monkeypatch.setattr(
        agent, "_retrieve_context", AsyncMock(return_value=("ctx", [])),
    )
    loop_spy = AsyncMock()
    monkeypatch.setattr(agent, "_run_tool_loop", loop_spy)

    async def _no_llm(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr("app.agents.conversation.get_llm", _no_llm)

    result = await agent.chat(SESSION_ID, "hello", USER_ID, project_id=None)
    loop_spy.assert_not_awaited()
    assert result["tool_trace"] == []
