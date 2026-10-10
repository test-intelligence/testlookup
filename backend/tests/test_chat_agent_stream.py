"""Ask-AI chat agent: one streamed turn, end to end through the REAL BudgetedLLM.

Each test drives ``ConversationAgent.start_turn`` with a scripted chat model
wrapped in ``BudgetedLLM`` -- the wrapper ``get_llm()`` returns in production.
The previous loop's tests mocked ``get_llm`` with a raw model, which is how a
loop that crashed on every message ("Expected a Runnable ... BudgetedLLM")
shipped green. Persistence is an in-memory store; the gates are stubbed the
way tests/services/test_llm_budgeted_stream.py stubs them.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from app.agents import conversation as conv
from app.core.config import settings
from app.services import llm_circuit_breaker, llm_cost_reservation as cost
from app.services.llm_cost_reservation import Reservation
from app.services.llm_factory import BudgetedLLM
from app.tools import chat_read_tools as crt

PROJECT = str(uuid.uuid4())
SESSION = str(uuid.uuid4())


class ProviderError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def call(name: str, **args: Any) -> dict:
    return {"name": name, "args": args, "id": f"c{uuid.uuid4().hex[:8]}"}


class ScriptedChatModel(BaseChatModel):
    """Streams scripted replies: text in pieces, then any tool calls.

    A script entry is an ``AIMessage`` (text and/or ``tool_calls``), an
    exception to raise, or a ``("sleep", seconds, AIMessage)`` tuple.
    Every call records the messages it was sent and its bound kwargs.
    """

    script: list = []
    calls: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self.bind(tool_names=[t.name for t in tools], **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # pragma: no cover
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="sync"))])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": dict(kwargs)})
        entry = self.script.pop(0) if self.script else AIMessage(content="(script exhausted)")
        delay = 0.0
        if isinstance(entry, tuple):
            _, delay, entry = entry
        if isinstance(entry, BaseException):
            raise entry
        words = entry.content.split(" ") if entry.content else []
        for i, word in enumerate(words):
            if delay:
                await asyncio.sleep(delay)
            yield ChatGenerationChunk(message=AIMessageChunk(content=word + (" " if i < len(words) - 1 else "")))
        for index, tc in enumerate(entry.tool_calls or []):
            yield ChatGenerationChunk(message=AIMessageChunk(
                content="",
                tool_call_chunks=[{
                    "name": tc["name"], "args": json.dumps(tc["args"]),
                    "id": tc.get("id"), "index": index,
                }],
            ))
        yield ChatGenerationChunk(message=AIMessageChunk(
            content="", usage_metadata={"input_tokens": 50, "output_tokens": 10, "total_tokens": 60},
        ))


@pytest.fixture
def gates(monkeypatch):
    breaker: dict = {"failure": []}

    async def available(*_a, **_k):
        return None

    async def reserve(provider, model, *, input_tokens, max_output_tokens):
        return Reservation(key="k", project_id=PROJECT, estimated_usd=0.01, ttl_seconds=60)

    async def settle(*_a, **_k):
        return None

    async def record_failure(provider, base_url, exc):
        breaker["failure"].append(exc)

    async def record_success(provider, base_url):
        return None

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", available)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(record_failure))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(record_success))
    monkeypatch.setattr(cost, "reserve", reserve)
    monkeypatch.setattr(cost, "settle", settle)
    return breaker


class Harness:
    """An agent with in-memory persistence and a scripted model."""

    def __init__(self, monkeypatch, script, *, provider="openrouter", mode="llm", lock=True):
        self.model = ScriptedChatModel(script=list(script), calls=[])
        self.llm = BudgetedLLM(self.model, provider=provider, model="scripted-model")
        self.store: list[dict] = []
        self.scopes: list = []
        self.agent = conv.ConversationAgent()
        conv._TOOLS_OFF.clear()

        async def get_llm(*_a, **_k):
            self.scopes.append(cost.current_cost_scope())
            return self.llm

        async def save(session_id, role, content, sources):
            row = {"id": str(uuid.uuid4()), "session_id": session_id, "role": role,
                   "content": content, "sources": sources, "created_at": "now"}
            self.store.append(row)
            return row

        async def history(session_id, before_id=None):
            rows = [r for r in self.store if r["role"] in ("user", "assistant")]
            ids = [r["id"] for r in rows]
            if before_id in ids:
                rows = rows[: ids.index(before_id)]
            out = []
            for a, b in zip(rows, rows[1:]):
                if a["role"] == "user" and b["role"] == "assistant":
                    out += [HumanMessage(content=a["content"]), AIMessage(content=b["content"])]
            return out, ""

        async def snapshot(project_id, limit=5):
            return ("| Build | Status |\n| 105 | FAILED |\nFailing tests in the most recent run (105): test_refund_flow",
                    [{"type": "test_run", "id": "run-105", "build": "105"},
                     {"type": "test_case", "id": "case-1", "run_id": "run-105", "name": "test_refund_flow"}])

        async def nothing(*_a, **_k):
            return None

        async def empty_text(*_a, **_k):
            return ""

        async def no_report(*_a, **_k):
            return "", []

        async def named(question, project_id):
            return [t for t in ("test_refund_flow",) if t in question]

        async def previous_answer(session_id, before_id=None):
            rows = [r for r in self.store if r["role"] in ("user", "assistant")]
            ids = [r["id"] for r in rows]
            if before_id in ids:
                rows = rows[: ids.index(before_id)]
            answers = [r["content"] for r in rows if r["role"] == "assistant"]
            return answers[-1] if answers else ""

        async def existing(candidates, project_id):
            return {c for c in candidates if c.startswith("test")}

        async def mode_value():
            return mode

        async def acquire(session_id):
            return "tok" if lock else None

        async def release(session_id, token):
            return None

        monkeypatch.setattr(conv, "get_llm", get_llm)
        monkeypatch.setattr(conv, "_configured_mode", mode_value)
        monkeypatch.setattr(conv, "_acquire_turn_lock", acquire)
        monkeypatch.setattr(conv, "_release_turn_lock", release)
        monkeypatch.setattr(self.agent, "_save_message", save)
        monkeypatch.setattr(self.agent, "_load_history", history)
        monkeypatch.setattr(self.agent, "_fetch_run_context", snapshot)
        monkeypatch.setattr(self.agent, "_fetch_project_name", lambda pid: _value("Checkout Service"))
        monkeypatch.setattr(self.agent, "_fetch_fingerprint_recall", empty_text)
        monkeypatch.setattr(self.agent, "_fetch_bound_report_context", no_report)
        monkeypatch.setattr(self.agent, "_named_tests", named)
        monkeypatch.setattr(self.agent, "_previous_answer", previous_answer)
        monkeypatch.setattr(self.agent, "_existing_tests", existing)
        monkeypatch.setattr(self.agent, "_touch_session", nothing)
        monkeypatch.setattr(self.agent, "_maybe_compress_history", nothing)

    def answered(self, question: str, answer: str) -> None:
        """An earlier question and its saved answer, in this conversation."""
        for role, content in (("user", question), ("assistant", answer)):
            self.store.append({"id": str(uuid.uuid4()), "session_id": SESSION, "role": role,
                               "content": content, "sources": None, "created_at": "earlier"})

    async def turn(self, question="What failed in the latest run and why?", *, project_id=PROJECT, retry=False, **kw):
        t = self.agent.start_turn(
            session_id=SESSION, user_id="u", project_id=project_id,
            user_message=question, retry=retry, **kw,
        )
        events = []
        while True:
            try:
                item = await t.next_event()
            except StopAsyncIteration:
                return events
            events.append(item)


async def _value(v):
    return v


def kinds(events):
    return [e for e, _ in events]


def data(events, kind):
    return [d for e, d in events if e == kind]


# ── The basic turn ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_turn_streams_text_and_saves_question_and_answer(monkeypatch, gates):
    h = Harness(monkeypatch, [
        AIMessage(content="", tool_calls=[call("list_run_failures", build_number="")]),
        AIMessage(content="Build 105 failed: test_refund_flow."),
    ])

    async def failures(state, build):
        return "Failing tests in build 105:\n- test_refund_flow", "listed 1 failing tests in build 105"

    monkeypatch.setattr(crt, "_fetch_run_failures", failures)
    events = await h.turn()

    assert kinds(events)[0] == "start"
    assert kinds(events)[1] == "status"  # immediate feedback, before any model call
    assert kinds(events)[-1] == "done"
    streamed = "".join(d["text"] for d in data(events, "delta"))
    done = data(events, "done")[0]
    assert streamed == "Build 105 failed: test_refund_flow." == done["message"]["content"]
    assert [r["role"] for r in h.store] == ["user", "assistant"]
    meta = done["meta"]
    assert meta["model"] == "scripted-model" and meta["provider"] == "openrouter"
    assert meta["first_token_ms"] is not None and meta["tool_calls"] == 1
    # The chips are the run and test the answer names, linkable.
    assert {s["type"] for s in done["sources"]} == {"test_run", "test_case"}


@pytest.mark.asyncio
async def test_a_tool_call_runs_and_its_result_reaches_the_model(monkeypatch, gates):
    """Regression: the tool loop runs through BudgetedLLM (it never had)."""
    h = Harness(monkeypatch, [
        AIMessage(content="", tool_calls=[call("get_test_history", test_name="test_checkout")]),
        AIMessage(content="It is a regression since build 104."),
    ])

    async def history(state, name):
        state.add_ref({"type": "test_run", "id": "run-104", "build": "104"})
        return "Pattern: a REGRESSION since build 104.", f"checked the history of '{name}'"

    monkeypatch.setattr(crt, "_fetch_test_history", history)
    events = await h.turn("Is test_checkout flaky or a regression?")

    second = h.model.calls[1]["messages"]
    tool_messages = [m for m in second if isinstance(m, ToolMessage)]
    assert tool_messages and "REGRESSION since build 104" in tool_messages[0].content
    asked = [m for m in second if isinstance(m, AIMessage) and m.tool_calls][0]
    assert tool_messages[0].tool_call_id == asked.tool_calls[0]["id"]
    labels = [d["label"] for d in data(events, "status")]
    assert "Checking the history of test_checkout…" in labels
    done = data(events, "done")[0]
    assert done["tool_trace"][0]["tool"] == "get_test_history"
    assert {"type": "test_run", "id": "run-104", "build": "104"} in done["sources"]


@pytest.mark.asyncio
async def test_the_first_round_must_call_a_tool_when_nothing_was_looked_up(monkeypatch, gates):
    h = Harness(monkeypatch, [AIMessage(content="", tool_calls=[call("list_recent_runs")]), AIMessage(content="ok")])
    monkeypatch.setattr(crt, "_fetch_recent_runs", lambda s, q: _value(("runs", "listed runs")))
    await h.turn("Is the first one you mentioned flaky?")
    assert h.model.calls[0]["kwargs"].get("tool_choice") == "required"
    assert "tool_choice" not in h.model.calls[1]["kwargs"]


@pytest.mark.asyncio
async def test_small_talk_is_not_forced_into_a_tool_call(monkeypatch, gates):
    h = Harness(monkeypatch, [AIMessage(content="You're welcome.")])
    await h.turn("thanks!")
    assert "tool_choice" not in h.model.calls[0]["kwargs"]


@pytest.mark.asyncio
async def test_a_named_test_is_looked_up_before_the_model_and_rides_with_the_question(monkeypatch, gates):
    """Measured: asked "Has test_refund_flow failed before?", the model did not
    call the history tool and wrote a table of builds that do not exist."""
    h = Harness(monkeypatch, [AIMessage(content="It failed only in build 105.")])

    async def history(state, name):
        return f"History of {name}: 105=FAILED, 104=PASSED", f"checked the history of '{name}'"

    async def quarantine(state, name):
        return f"No quarantine record for {name}.", "checked quarantine"

    monkeypatch.setattr(crt, "_fetch_test_history", history)
    monkeypatch.setattr(crt, "_fetch_quarantine_status", quarantine)
    events = await h.turn("Has test_refund_flow failed before, and is it quarantined?")

    sent = h.model.calls[0]["messages"]
    question = sent[-1]
    assert isinstance(question, HumanMessage)
    assert "Looked up for this question" in question.content
    assert "105=FAILED" in question.content and "No quarantine record" in question.content
    assert "tool_choice" not in h.model.calls[0]["kwargs"]  # already grounded
    labels = [d["label"] for d in data(events, "status")]
    assert "Checking the history of test_refund_flow…" in labels
    assert "Checking quarantine status of test_refund_flow…" in labels
    # The footer's count is every lookup, not only the model's calls (it said
    # nothing under "How I looked this up · 2 checks").
    meta = data(events, "done")[0]["meta"]
    assert meta["tool_calls"] == 0 and meta["lookups"] == 2
    # The saved question is the user's words, not the lookups.
    assert h.store[0]["content"] == "Has test_refund_flow failed before, and is it quarantined?"


@pytest.mark.asyncio
async def test_a_release_question_gets_the_gate_verdict_up_front(monkeypatch, gates):
    h = Harness(monkeypatch, [AIMessage(content="NO_GO.")])
    monkeypatch.setattr(crt, "_fetch_release_gate", lambda s, q: _value(("Verdict: NO_GO (risk 71)", "checked release gate")))
    await h.turn("Is the latest build ready to release?")
    assert "Verdict: NO_GO (risk 71)" in h.model.calls[0]["messages"][-1].content


@pytest.mark.asyncio
async def test_which_tests_are_flaky_gets_the_list_up_front_but_the_first_one_does_not(monkeypatch, gates):
    """Measured: asked which tests are flaky, the model guessed two names from
    the previous answer and reported them. But "is the first one flaky?" is
    about one earlier test: no list, and the model must look that test up."""
    flaky_calls = []

    async def flaky(state, q):
        flaky_calls.append(q)
        return "| testCheckout03 | ... | none |", "listed flaky tests (30d) — 1 found, 0 quarantined"

    monkeypatch.setattr(crt, "_fetch_flaky_tests", flaky)
    h = Harness(monkeypatch, [AIMessage(content="testCheckout03 is flaky; none quarantined.")])
    await h.turn("Which tests are flaky right now? Are any quarantined?")
    assert flaky_calls and "testCheckout03" in h.model.calls[0]["messages"][-1].content

    flaky_calls.clear()
    h = Harness(monkeypatch, [AIMessage(content="", tool_calls=[call("get_test_history", test_name="x")]),
                              AIMessage(content="ok")])
    monkeypatch.setattr(crt, "_fetch_test_history", lambda s, n: _value(("history", "checked")))
    await h.turn("Is the first one you mentioned flaky?")
    assert flaky_calls == []
    assert h.model.calls[0]["kwargs"].get("tool_choice") == "required"


# The two answers the homelab measurement (2026-10-09) followed up on. Their
# first rows are NOT the first failure the snapshot lists (Case02, by AI
# confidence), which is the test the model answered about both times.
FAILED_TABLE = (
    "In build-2029, the following tests failed:\n\n"
    "| Test Name | Suite | Status | Error |\n|---|---|---|---|\n"
    "| testAuthenticationCase01 | AuthSuite | FAILED | AssertionError: JWT token expiry mismatch |\n"
    "| testAuthenticationCase02 | AuthSuite | FAILED | TimeoutException: Session validation timed out |\n"
    "| testSearchCase05 | SearchSuite | FAILED | AssertionError: Search returned 24 results |"
)
FLAKY_TABLE = (
    "Here are the flaky tests in the last 30 days:\n\n"
    "| Test | Suite | Runs | Failed | Failure rate |\n|---|---|---|---|---|\n"
    "| **testCheckoutCase03** | CheckoutSuite | 22 | 8 | 36.4% |\n"
    "| `testAuthenticationCase04` | AuthSuite | 30 | 7 | 23.3% |\n"
    "| testAuthenticationCase02 | AuthSuite | 30 | 5 | 16.7% |\n\n"
    "None of these tests are currently quarantined."
)


def _record_history_and_quarantine(monkeypatch):
    looked_up: list[tuple[str, str]] = []

    async def history(state, name):
        looked_up.append(("history", name))
        return f"History of {name}: 2029=FAILED, 2028=PASSED", f"checked the history of '{name}'"

    async def quarantine(state, name):
        looked_up.append(("quarantine", name))
        return f"No quarantine record for {name}.", "checked quarantine"

    monkeypatch.setattr(crt, "_fetch_test_history", history)
    monkeypatch.setattr(crt, "_fetch_quarantine_status", quarantine)
    return looked_up


@pytest.mark.asyncio
async def test_the_first_one_is_the_first_test_the_previous_answer_lists(monkeypatch, gates):
    """Measured: "Is the first one flaky or a regression?" after a table whose
    first row was testAuthenticationCase01 got testAuthenticationCase02's
    history -- the first failure in the snapshot, not in the answer."""
    looked_up = _record_history_and_quarantine(monkeypatch)
    h = Harness(monkeypatch, [AIMessage(content="testAuthenticationCase01 is new in build-2029.")])
    h.answered("What failed in build-2029, and why?", FAILED_TABLE)
    events = await h.turn("Is the first one flaky or a regression? When did it start failing?")

    assert looked_up == [("history", "testAuthenticationCase01")]
    asked = h.model.calls[0]["messages"][-1].content
    assert '"the first one" is testAuthenticationCase01: the first test your previous answer lists' in asked
    assert "History of testAuthenticationCase01: 2029=FAILED" in asked
    assert "tool_choice" not in h.model.calls[0]["kwargs"]  # grounded before the model
    labels = [d["label"] for d in data(events, "status")]
    assert "Checking the history of testAuthenticationCase01…" in labels
    # The saved question is still the user's words.
    assert h.store[-2]["content"] == "Is the first one flaky or a regression? When did it start failing?"


@pytest.mark.asyncio
async def test_the_top_one_and_the_last_one_and_quarantine(monkeypatch, gates):
    """Measured: "the top one" after the flaky-tests table (testCheckoutCase03
    first) got testAuthenticationCase02's history, and "should we quarantine
    it?" went unanswered. Bold and code-formatted names count as listed."""
    looked_up = _record_history_and_quarantine(monkeypatch)
    h = Harness(monkeypatch, [AIMessage(content="ok")])
    h.answered("Which tests are flaky right now?", FLAKY_TABLE)
    await h.turn("What does the history of the top one look like? Should we quarantine it?")
    assert looked_up == [("history", "testCheckoutCase03"), ("quarantine", "testCheckoutCase03")]
    assert "No quarantine record for testCheckoutCase03." in h.model.calls[0]["messages"][-1].content

    looked_up.clear()
    h = Harness(monkeypatch, [AIMessage(content="ok")])
    h.answered("Which tests are flaky right now?", FLAKY_TABLE)
    await h.turn("Is the last test a regression?")
    assert looked_up == [("history", "testAuthenticationCase02")]


@pytest.mark.parametrize("question", [
    "Is the fifth one flaky?",  # the answer lists three
    "Has the first new failure failed before?",  # a sub-list: measured, the model resolves it
])
@pytest.mark.asyncio
async def test_a_reference_the_answer_cannot_settle_is_left_to_the_model(monkeypatch, gates, question):
    looked_up = _record_history_and_quarantine(monkeypatch)
    h = Harness(monkeypatch, [AIMessage(content="", tool_calls=[call("list_recent_runs")]), AIMessage(content="ok")])
    monkeypatch.setattr(crt, "_fetch_recent_runs", lambda s, q: _value(("runs", "listed runs")))
    h.answered("What failed in build-2029, and why?", FAILED_TABLE)
    await h.turn(question)
    assert looked_up == []
    assert h.model.calls[0]["kwargs"].get("tool_choice") == "required"


def test_test_name_candidates_keep_the_order_the_answer_writes_them():
    assert conv._test_name_candidates(FLAKY_TABLE)[:4] == [
        "testCheckoutCase03", "CheckoutSuite", "testAuthenticationCase04", "AuthSuite",
    ]
    assert conv._test_name_candidates(FAILED_TABLE)[0] == "build-2029"


@pytest.mark.asyncio
async def test_the_round_cap_forces_an_answer(monkeypatch, gates):
    monkeypatch.setattr(settings, "CHAT_MAX_TOOL_ROUNDS", 2)
    looping = [AIMessage(content="", tool_calls=[call("list_recent_runs")]) for _ in range(2)]
    h = Harness(monkeypatch, looping + [AIMessage(content="Here is what I found.")])
    monkeypatch.setattr(crt, "_fetch_recent_runs", lambda s, q: _value(("runs", "listed runs")))
    events = await h.turn("How are the recent runs trending?")

    assert len(h.model.calls) == 3
    assert h.model.calls[2]["kwargs"].get("tool_choice") == "none"
    assert data(events, "done")[0]["message"]["content"] == "Here is what I found."


@pytest.mark.asyncio
async def test_tool_calls_beyond_the_per_round_cap_are_dropped(monkeypatch, gates):
    many = AIMessage(content="", tool_calls=[call("list_recent_runs") for _ in range(10)])
    h = Harness(monkeypatch, [many, AIMessage(content="done")])
    monkeypatch.setattr(crt, "_fetch_recent_runs", lambda s, q: _value(("runs", "listed runs")))
    events = await h.turn("How are the recent runs trending?")
    assert data(events, "done")[0]["meta"]["tool_calls"] == conv._MAX_CALLS_PER_ROUND


@pytest.mark.asyncio
async def test_a_model_without_tool_use_answers_without_tools_and_is_remembered(monkeypatch, gates):
    refusal = ProviderError("No endpoints found that support tool use", 404)
    h = Harness(monkeypatch, [refusal, AIMessage(content="From the snapshot: build 105 failed.")])
    events = await h.turn()

    assert data(events, "done")[0]["meta"]["tools"] is False
    assert "tool_names" not in h.model.calls[1]["kwargs"]
    assert ("openrouter", "scripted-model") in conv._TOOLS_OFF
    assert gates["failure"] == []  # not an outage


# ── Failures: typed, and never saved as an answer ───────────────────────────


@pytest.mark.asyncio
async def test_a_provider_failure_is_a_typed_error_and_no_answer_is_saved(monkeypatch, gates):
    """The old path saved "I'm having trouble connecting..." as the assistant's
    answer, then replayed it to the model as history."""
    h = Harness(monkeypatch, [ProviderError("bad gateway", 502)])
    events = await h.turn()

    assert kinds(events)[-1] == "error"
    error = data(events, "error")[0]
    assert error["code"] == "provider_unavailable" and error["retryable"] is True
    assert [r["role"] for r in h.store] == ["user"]


@pytest.mark.asyncio
async def test_a_stalled_provider_is_reported_as_a_timeout(monkeypatch, gates):
    monkeypatch.setattr(settings, "CHAT_FIRST_TOKEN_TIMEOUT_SECONDS", 0.05)
    h = Harness(monkeypatch, [("sleep", 0.5, AIMessage(content="too late"))])
    events = await h.turn("thanks a lot!")
    error = data(events, "error")[0]
    assert error["code"] == "timeout" and error["retryable"] is True
    assert len(gates["failure"]) == 1  # counted by the breaker


@pytest.mark.asyncio
async def test_no_project_and_rules_mode_and_no_provider_are_said_plainly(monkeypatch, gates):
    h = Harness(monkeypatch, [])
    assert data(await h.turn(project_id=None), "error")[0]["code"] == "project_required"

    h = Harness(monkeypatch, [], mode="rules")
    error = data(await h.turn(), "error")[0]
    assert error["code"] == "llm_disabled" and "Chat is unavailable in Rules mode" in error["message"]

    h = Harness(monkeypatch, [])

    async def broken(*_a, **_k):
        raise ValueError("OpenRouter API key not configured. Set OPENROUTER_API_KEY")

    monkeypatch.setattr(conv, "get_llm", broken)
    error = data(await h.turn(), "error")[0]
    assert error["code"] == "llm_unavailable" and "OPENROUTER_API_KEY" in error["message"]


@pytest.mark.asyncio
async def test_a_second_turn_in_the_same_conversation_is_refused(monkeypatch, gates):
    h = Harness(monkeypatch, [AIMessage(content="x")], lock=False)
    events = await h.turn()
    assert kinds(events) == ["error"]
    assert data(events, "error")[0]["code"] == "turn_in_progress"
    assert h.store == [] and h.model.calls == []


# ── Stop, retry, history ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_keeps_the_text_already_shown_marked_stopped(monkeypatch, gates):
    h = Harness(monkeypatch, [("sleep", 0.05, AIMessage(content="one two three four five six seven"))])
    turn = h.agent.start_turn(session_id=SESSION, user_id="u", project_id=PROJECT, user_message="thanks so much")
    while True:
        event, payload = await turn.next_event()
        if event == "delta":
            break
    turn.cancel_nowait()
    await turn.wait_closed()

    answers = [r for r in h.store if r["role"] == "assistant"]
    assert len(answers) == 1
    assert answers[0]["content"].startswith("one")
    assert conv._is_stopped(answers[0]["sources"])
    # The footer names the model for a stopped answer too (homelab, 2026-10-10:
    # "first words in 5.5 s · stopped after 6.2 s", no model).
    meta = next(s for s in answers[0]["sources"] if s.get("type") == "meta")
    assert (meta["provider"], meta["model"]) == ("openrouter", "scripted-model")


@pytest.mark.asyncio
async def test_stop_during_the_final_save_does_not_save_a_second_copy(monkeypatch, gates):
    h = Harness(monkeypatch, [AIMessage(content="The full answer.")])
    original = h.agent._save_message
    saving = asyncio.Event()

    async def slow_save(session_id, role, content, sources):
        if role == "assistant" and not conv._is_stopped(sources):
            saving.set()
            await asyncio.sleep(0.1)
        return await original(session_id, role, content, sources)

    monkeypatch.setattr(h.agent, "_save_message", slow_save)
    turn = h.agent.start_turn(session_id=SESSION, user_id="u", project_id=PROJECT, user_message="thanks so much")
    await saving.wait()
    turn.cancel_nowait()
    await turn.wait_closed()
    await asyncio.sleep(0.2)  # the shielded save completes

    answers = [r for r in h.store if r["role"] == "assistant"]
    assert [a["content"] for a in answers] == ["The full answer."]
    assert not conv._is_stopped(answers[0]["sources"])


@pytest.mark.asyncio
async def test_retry_answers_the_same_question_without_saving_it_again(monkeypatch, gates):
    h = Harness(monkeypatch, [AIMessage(content="Second try.")])
    h.store.append({"id": "q1", "session_id": SESSION, "role": "user",
                    "content": "What failed in build 105?", "sources": None, "created_at": "t"})

    async def prepare(session_id):
        return "What failed in build 105?", "q1"

    monkeypatch.setattr(h.agent, "_prepare_retry", prepare)
    events = await h.turn(question=None, retry=True)

    assert data(events, "start")[0] == {"user_message_id": "q1", "retry": True}
    assert [r["role"] for r in h.store] == ["user", "assistant"]
    assert h.model.calls[0]["messages"][-1].content.startswith("What failed in build 105?")


@pytest.mark.asyncio
async def test_the_conversation_so_far_reaches_the_model(monkeypatch, gates):
    h = Harness(monkeypatch, [
        AIMessage(content="test_refund_flow and test_discount_stacking failed."),
        AIMessage(content="", tool_calls=[call("get_test_history", test_name="test_refund_flow")]),
        AIMessage(content="It started failing in build 105."),
    ])
    monkeypatch.setattr(crt, "_fetch_test_history", lambda s, n: _value(("105=FAILED", "checked")))
    await h.turn("thanks, what failed?")
    await h.turn("When did the first one start failing?")

    second_turn = h.model.calls[1]["messages"]
    replayed = [m.content for m in second_turn if isinstance(m, (HumanMessage, AIMessage))]
    assert "thanks, what failed?" in replayed
    assert "test_refund_flow and test_discount_stacking failed." in replayed


# ── Pure helpers ─────────────────────────────────────────────────────────────


def test_a_slot_timeout_is_busy_not_a_provider_timeout():
    from app.services.llm_cluster_semaphore import LLMSlotTimeout

    assert conv.classify_failure(LLMSlotTimeout("full")).code == "busy"
    assert conv.classify_failure(TimeoutError()).code == "timeout"
    assert conv.classify_failure(ProviderError("x", 401)).code == "provider_auth"
    assert conv.classify_failure(ProviderError("x", 429)).code == "rate_limited"


def test_answer_sources_are_what_the_answer_names():
    refs = [
        {"type": "test_run", "id": "r1", "build": "105"},
        {"type": "test_run", "id": "r2", "build": "104"},
        {"type": "test_case", "id": "c1", "run_id": "r1", "name": "test_refund_flow"},
        {"type": "test_case", "id": "c1", "run_id": "r1", "name": "test_refund_flow"},
    ]
    reply = "Build 105 failed: test_refund_flow. 104 tests passed."
    assert conv._answer_sources(reply, refs) == [refs[0], refs[2]]


def test_answer_sources_follow_the_order_the_answer_names_them():
    """The snapshot lists failures by AI confidence; chips in that order put
    testAuthenticationCase02 first under a table that starts with Case01."""
    refs = [
        {"type": "test_case", "id": "c2", "run_id": "r", "name": "testAuthenticationCase02"},
        {"type": "test_case", "id": "c1", "run_id": "r", "name": "testAuthenticationCase01"},
        {"type": "test_run", "id": "r", "build": "build-2029"},
    ]
    chips = conv._answer_sources(FAILED_TABLE, refs)
    assert [c.get("name") or c.get("build") for c in chips] == [
        "build-2029", "testAuthenticationCase01", "testAuthenticationCase02",
    ]


def test_history_pairs_skip_unanswered_questions(monkeypatch):
    rows = [
        type("R", (), {"id": "1", "role": "user", "content": "q1"}),
        type("R", (), {"id": "2", "role": "assistant", "content": "a1"}),
        type("R", (), {"id": "3", "role": "user", "content": "q2 (failed turn)"}),
        type("R", (), {"id": "4", "role": "user", "content": "q3"}),
        type("R", (), {"id": "5", "role": "assistant", "content": "a3"}),
        type("R", (), {"id": "6", "role": "user", "content": "now"}),
    ]

    class Result:
        def __init__(self, items, first=None):
            self.items, self._first = items, first

        def all(self):
            return self.items

        def first(self):
            return self._first

    class DB:
        def __init__(self):
            self.n = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, _stmt):
            self.n += 1
            return Result(list(reversed(rows))) if self.n == 1 else Result([], None)

    monkeypatch.setattr(conv, "AsyncSessionLocal", lambda: DB())
    pairs, summary = asyncio.run(conv.ConversationAgent()._load_history("s", before_id="6"))
    assert [m.content for m in pairs] == ["q1", "a1", "q3", "a3"]
    assert summary == ""


def test_question_shapes():
    assert conv._wants_data("Is the first one flaky?")
    assert not conv._wants_data("thanks!")
    assert not conv._wants_data("hi")
    assert conv._looks_like_a_test_name("test_refund_flow")
    assert conv._looks_like_a_test_name("testAuthenticationCase02")
    assert not conv._looks_like_a_test_name("quarantined")


def test_tool_calls_without_ids_or_with_bad_json_are_kept_and_answered():
    message = AIMessageChunk(content="")
    message.tool_calls = [{"name": "list_recent_runs", "args": {}, "id": None, "type": "tool_call"}]
    message.invalid_tool_calls = [{"name": "get_test_history", "args": "{bad", "id": None, "error": "bad json", "type": "invalid_tool_call"}]
    calls = conv._tool_calls_of(message)
    assert [c["name"] for c in calls] == ["list_recent_runs", "get_test_history"]
    assert all(len(c["id"]) == 9 and c["id"].isalnum() for c in calls)
    assert calls[1]["error"] == "bad json"
