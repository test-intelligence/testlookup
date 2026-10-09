"""Ask-AI chat read tools — tenancy, budgets, trace and action handoffs (AI-6).

Pins:
  * every read tool is project-scoped by a server-side ContextVar (the LLM
    never supplies identifiers) — no context ⇒ "unavailable", and the SQL
    each fetcher issues binds the CALLER's project id (leakage guard);
  * per-call + total token budgets on tool observations;
  * suggested_actions emit deterministic quarantine / Jira handoffs from
    structured tool findings — never from LLM text;
  * the 2026-10-09 tools (test history, build comparison, flaky list) state
    what the data says, including the pattern, so the model does not have to.

The turn itself (streaming, tool calls through the real BudgetedLLM, errors,
stop, retry) is pinned in tests/test_chat_agent_stream.py. All DB access is
stubbed — no live services required.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("langchain")

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
        (crt._fetch_test_history, "test_checkout", 2),
        (crt._fetch_build_comparison, "", 1),
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


# ── (f) The 2026-10-09 tools: what the data says, stated by the server ───────


def _at(day: int) -> datetime:
    return datetime(2026, 10, day, 12, 0, tzinfo=timezone.utc)


def _history_rows(statuses):
    """Newest first, one run each."""
    return [
        SimpleNamespace(
            run_id=uuid.uuid4(), build_number=f"build-{2030 - i}", start_time=_at(28 - i),
            case_id=uuid.uuid4(), status=status,
            error_message="TimeoutException: session" if status == "FAILED" else None,
        )
        for i, status in enumerate(statuses)
    ]


async def _history_output(statuses, monkeypatch):
    match = SimpleNamespace(test_name="testAuth02", test_fingerprint="fp-a", suite_name="AuthSuite")
    db = _FakeDB(results=[_FakeResult(first=match), _FakeResult(rows=_history_rows(statuses))])

    async def window(db, project_id, fingerprint):
        return {"total_runs": 0}

    monkeypatch.setattr("app.services.test_case_history_service._flakiness_in_window", window)
    token = crt.set_chat_tool_context(project_id=PROJECT_A)
    try:
        state = crt.get_chat_tool_state()
        with patch("app.db.postgres.AsyncSessionLocal", return_value=db):
            text, summary = await crt._fetch_test_history(state, "testAuth02")
        return text, summary, state
    finally:
        crt.reset_chat_tool_context(token)


@pytest.mark.asyncio
async def test_history_names_a_regression_and_where_it_started(monkeypatch):
    """Measured: given 3 failures after 8 passes, the model called the test
    "intermittent, which suggests it is flaky" and put the start one build late."""
    text, summary, state = await _history_output(["FAILED"] * 3 + ["PASSED"] * 8 + ["FAILED"], monkeypatch)
    assert "Pattern: a REGRESSION" in text
    assert "failing in the last 3 run(s) in a row (build-2028, build-2029, build-2030)" in text
    assert "first failure of this streak was build-2028" in text
    assert "last passed in build-2027" in text
    assert "build build-" not in text  # the double label the model misread
    assert "4 of 12 recent runs failed" in summary
    assert any(r["type"] == "test_case" and r["name"] == "testAuth02" for r in state.refs.values())


@pytest.mark.asyncio
async def test_history_names_intermittent_failures(monkeypatch):
    text, _summary, _state = await _history_output(["PASSED", "FAILED", "PASSED", "FAILED", "PASSED", "FAILED"], monkeypatch)
    assert "Pattern: INTERMITTENT" in text


@pytest.mark.asyncio
async def test_history_does_not_call_a_single_failure_a_regression(monkeypatch):
    text, _summary, _state = await _history_output(["FAILED", "PASSED", "PASSED", "PASSED"], monkeypatch)
    assert "too early to tell" in text


@pytest.mark.asyncio
async def test_history_asks_which_test_when_a_partial_name_matches_several():
    rows = [
        SimpleNamespace(test_name="test_login_ok", test_fingerprint="a", suite_name="s"),
        SimpleNamespace(test_name="test_login_bad", test_fingerprint="b", suite_name="s"),
    ]
    db = _FakeDB(results=[_FakeResult(), _FakeResult(rows=rows)])
    token = crt.set_chat_tool_context(project_id=PROJECT_A)
    try:
        with patch("app.db.postgres.AsyncSessionLocal", return_value=db):
            text, _ = await crt._fetch_test_history(crt.get_chat_tool_state(), "test_login")
    finally:
        crt.reset_chat_tool_context(token)
    assert "Several tests match" in text and "test_login_ok" in text and "test_login_bad" in text


@pytest.mark.parametrize("text,tokens", [
    ("", []),
    ("105", ["105"]),
    ("104 vs 105", ["104", "105"]),
    ("build 104 and build 105", ["104", "105"]),
    ("#2027 → #2029", ["2027", "2029"]),
    ("compare build-2027 with build-2029", ["build-2027", "build-2029"]),
])
def test_build_tokens(text, tokens):
    assert crt._build_tokens(text) == tokens


@pytest.mark.asyncio
async def test_comparison_defaults_to_the_previous_run_of_the_same_suite(monkeypatch):
    newer = SimpleNamespace(id=uuid.uuid4(), build_number="105", start_time=_at(7), primary_suite_name="api")
    older = SimpleNamespace(id=uuid.uuid4(), build_number="103", start_time=_at(6), primary_suite_name="api")
    db = _FakeDB(results=[_FakeResult(first=newer), _FakeResult(first=older)])
    compared = {}

    async def fake_compare(db, left, right):
        compared["pair"] = (left, right)
        return {
            "left": {"pass_rate": 83.3, "failed_tests": 1, "broken_tests": 1, "total_tests": 12},
            "right": {"pass_rate": 83.3, "failed_tests": 2, "broken_tests": 0, "total_tests": 12},
            "new_failures": 1, "fixed": 1, "still_failing": 1, "new_tests": 0, "removed_tests": 0,
            "test_deltas": [
                {"classification": "new_failure", "test_name": "test_refund_flow", "suite_name": "api",
                 "left_status": "PASSED", "right_status": "FAILED"},
                {"classification": "fixed", "test_name": "test_currency_rounding", "suite_name": "regression",
                 "left_status": "BROKEN", "right_status": "PASSED"},
            ],
        }

    monkeypatch.setattr("app.services.run_compare_service.compare_runs", fake_compare)
    token = crt.set_chat_tool_context(project_id=PROJECT_A)
    try:
        state = crt.get_chat_tool_state()
        with patch("app.db.postgres.AsyncSessionLocal", return_value=db):
            text, summary = await crt._fetch_build_comparison(state, "")
    finally:
        crt.reset_chat_tool_context(token)
    assert compared["pair"] == (older.id, newer.id)
    assert text.startswith("Build 103 → build 105 (suite api):")
    assert "New failures (1):" in text and "test_refund_flow" in text
    assert "Fixed (1):" in text and "test_currency_rounding" in text
    assert summary.endswith("1 new failure(s), 1 fixed")
    # The previous-run query was the same-suite one.
    assert "primary_suite_name" in str(db.statements[1])


@pytest.mark.asyncio
async def test_the_flaky_list_carries_quarantine_state(monkeypatch):
    async def flaky(db, project_id, days, limit):
        assert project_id == PROJECT_A and days == 30
        return {"total": 2, "items": [
            {"test_fingerprint": "fp-1", "test_name": "testCheckout03", "suite_name": "Checkout",
             "total_runs": 22, "fail_count": 8, "pass_count": 14, "failure_rate_pct": 36.4, "source": "auto"},
            {"test_fingerprint": "fp-2", "test_name": "testAuth04", "suite_name": "Auth",
             "total_runs": 30, "fail_count": 7, "pass_count": 22, "failure_rate_pct": 23.3, "source": "auto"},
        ]}

    monkeypatch.setattr("app.services.analytics_service.flaky_tests", flaky)
    db = _FakeDB(results=[_FakeResult(rows=[("fp-1", "QUARANTINED")])])
    token = crt.set_chat_tool_context(project_id=PROJECT_A)
    try:
        with patch("app.db.postgres.AsyncSessionLocal", return_value=db):
            text, summary = await crt._fetch_flaky_tests(crt.get_chat_tool_state(), "")
    finally:
        crt.reset_chat_tool_context(token)
    assert "| testCheckout03 | Checkout | 22 | 8 | 14 | 36.4% | auto | QUARANTINED |" in text
    assert "| testAuth04 | Auth | 30 | 7 | 22 | 23.3% | auto | none |" in text
    assert "1 of the tests shown are actively quarantined" in text
    assert summary.endswith("2 found, 1 quarantined")


@pytest.mark.asyncio
async def test_the_release_gate_says_when_the_latest_run_has_no_verdict():
    """Ordered by when the decision row was written, the homelab's E-Commerce
    Platform answered with build-2000's verdict while build-2029 was latest."""
    latest = SimpleNamespace(id=uuid.uuid4(), build_number="build-2029")
    verdict = SimpleNamespace(
        recommendation="CONDITIONAL_GO", risk_score=48, blocking_issues=[], reasoning=None,
        run_id=uuid.uuid4(), build_number="build-2000",
    )
    db = _FakeDB(results=[_FakeResult(first=latest), _FakeResult(first=verdict)])
    token = crt.set_chat_tool_context(project_id=PROJECT_A)
    try:
        with patch("app.db.postgres.AsyncSessionLocal", return_value=db):
            text, _ = await crt._fetch_release_gate(crt.get_chat_tool_state(), "")
    finally:
        crt.reset_chat_tool_context(token)
    assert text.startswith("The latest run (build-2029) has NO release-gate verdict.")
    assert "verdict for build-2000: **CONDITIONAL_GO**" in text
    # Ordered by the run, not by when the decision was written.
    assert "test_runs.start_time DESC" in str(db.statements[1])


@pytest.mark.asyncio
async def test_recent_runs_survive_a_run_in_progress():
    """``:.1f`` on a None pass rate raised, and the tool answered "unavailable"."""
    rows = [
        SimpleNamespace(id=uuid.uuid4(), build_number="106", branch="main", status="IN_PROGRESS",
                        total_tests=3, failed_tests=0, pass_rate=None, start_time=_at(8)),
        SimpleNamespace(id=uuid.uuid4(), build_number="105", branch="main", status="FAILED",
                        total_tests=12, failed_tests=2, pass_rate=83.3, start_time=_at(7)),
    ]
    db = _FakeDB(results=[_FakeResult(rows=rows)])
    token = crt.set_chat_tool_context(project_id=PROJECT_A)
    try:
        with patch("app.db.postgres.AsyncSessionLocal", return_value=db):
            text, summary = await crt._fetch_recent_runs(crt.get_chat_tool_state(), "")
    finally:
        crt.reset_chat_tool_context(token)
    assert "| 106 | main | IN_PROGRESS | 3 | 0 | n/a |" in text
    assert "83.3%" in text and "n/a pass rate" in summary


@pytest.mark.parametrize("name,args,label", [
    ("get_test_history", {"test_name": "test_refund_flow"}, "Checking the history of test_refund_flow…"),
    ("list_run_failures", {"build_number": "105"}, "Reading the failures in build 105…"),
    ("list_run_failures", {"build_number": "build-2029"}, "Reading the failures in build-2029…"),
    ("list_run_failures", {"build_number": ""}, "Reading the latest run's failures…"),
    ("compare_builds", {"builds": ""}, "Comparing the latest build with the previous one…"),
    ("no_such_tool", {}, "Running no_such_tool…"),
])
def test_status_labels(name, args, label):
    assert crt.tool_status_label(name, args) == label
