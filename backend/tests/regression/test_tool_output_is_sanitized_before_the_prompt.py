"""Tool output must pass through ``sanitize_tool_output`` before the next prompt.

Re-audit finding M17. ``sanitize_tool_output`` exists "to sanitise tool output
before it propagates into action agents" -- and its only callers were its own
unit tests. Both ReAct loops feed each tool's return value straight back into
the next prompt:

* the triage agent (``services/agent.py``), whose tools read Allure results,
  REST payloads, Splunk and OpenShift events;
* the chat copilot (``tools/chat_read_tools.py``), whose tools return failure
  text, suite names and error messages.

All of that is written by whatever is under test. The observation sinks
further down -- citations, trace spans, the audit trail -- were already
scrubbed with ``sanitize_reference_text``; the prompt, the one that matters,
was not. That is why the finding read "unfiltered" while grep found sanitizers
nearby.

What these tests prove is the WIRING: that both choke points now call the
function. They observe it through secret redaction and the length cap, which
are part of the same function. Its injection-neutralising behaviour has its own
coverage in ``tests/test_phase4_safety.py``, and is deliberately not restated
here with literal payload strings.
"""
from __future__ import annotations

import pytest
from langchain_core.tools import tool

from app.services import agent
from app.services.input_sanitizer import MAX_FREE_TEXT
from app.tools import chat_read_tools

pytestmark = pytest.mark.regression

LEAKED = "connected with password=hunter2xyz to the payments db"
TOO_LONG = "x" * (MAX_FREE_TEXT * 2)


@tool
async def _leaky_async_tool(query: str = "") -> str:
    """Returns text containing a credential, as an external system might."""
    return LEAKED


@tool
def _leaky_sync_tool(query: str = "") -> str:
    """Synchronous variant."""
    return LEAKED


@tool
async def _huge_async_tool(query: str = "") -> str:
    """Returns far more text than a prompt should carry."""
    return TOO_LONG


# ── Triage agent ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_async_tools_output_is_sanitized():
    out = await agent._sanitizing_copy(_leaky_async_tool).ainvoke({"query": "x"})
    assert "hunter2xyz" not in out, (
        "a credential in tool output reached the prompt — sanitize_tool_output "
        "is not on the triage agent's tool path"
    )
    assert "[REDACTED]" in out


def test_a_sync_tools_output_is_sanitized():
    out = agent._sanitizing_copy(_leaky_sync_tool).invoke({"query": "x"})
    assert "hunter2xyz" not in out


@pytest.mark.asyncio
async def test_an_oversized_output_is_capped():
    out = await agent._sanitizing_copy(_huge_async_tool).ainvoke({"query": "x"})
    assert len(out) < len(TOO_LONG)
    assert "[truncated at" in out


def test_the_copy_keeps_the_tools_identity():
    """The model chooses tools by name and argument schema; neither may change."""
    wrapped = agent._sanitizing_copy(_leaky_async_tool)
    assert wrapped.name == _leaky_async_tool.name
    assert wrapped.args == _leaky_async_tool.args
    assert wrapped.description == _leaky_async_tool.description


def test_the_module_level_tool_is_never_modified():
    """_get_tools runs per analysis; wrapping in place would stack a layer each time."""
    original = _leaky_async_tool.coroutine
    agent._sanitizing_copy(_leaky_async_tool)
    agent._sanitizing_copy(_leaky_async_tool)
    assert _leaky_async_tool.coroutine is original


@pytest.mark.asyncio
async def test_repeated_builds_do_not_stack():
    first = await agent._sanitizing_copy(_leaky_async_tool).ainvoke({"query": "x"})
    second = await agent._sanitizing_copy(_leaky_async_tool).ainvoke({"query": "x"})
    assert first == second


def test_every_triage_tool_reaches_the_executor_wrapped():
    """None of the six may be handed to the executor as the raw singleton."""
    from app.tools.analyze_ocp import analyze_openshift_pod_events
    from app.tools.check_flakiness import check_test_flakiness
    from app.tools.fetch_rest_payload import fetch_rest_api_payload
    from app.tools.fetch_stacktrace import fetch_allure_stacktrace
    from app.tools.query_splunk import query_splunk_logs
    from app.tools.recall_memory import recall_similar_failures

    originals = {
        t.name: t
        for t in (
            fetch_allure_stacktrace,
            fetch_rest_api_payload,
            query_splunk_logs,
            check_test_flakiness,
            analyze_openshift_pod_events,
            recall_similar_failures,
        )
    }
    built = agent._get_tools()
    assert {t.name for t in built} == set(originals)
    for t in built:
        src = originals[t.name]
        assert t is not src, f"{t.name} reaches the executor unwrapped"
        if getattr(src, "coroutine", None) is not None:
            assert t.coroutine is not src.coroutine, f"{t.name}'s coroutine is unwrapped"
        if getattr(src, "func", None) is not None:
            assert t.func is not src.func, f"{t.name}'s func is unwrapped"


# ── Chat copilot ─────────────────────────────────────────────────────────


@pytest.fixture
def chat_context():
    token = chat_read_tools.set_chat_tool_context(project_id="proj-1")
    try:
        yield
    finally:
        chat_read_tools.reset_chat_tool_context(token)


@pytest.mark.asyncio
async def test_chat_tool_output_is_sanitized(chat_context):
    async def _fetch(_state, _q):
        return LEAKED, "summary"

    out = await chat_read_tools._run_bounded("list_recent_runs", _fetch)
    assert "hunter2xyz" not in out, (
        "a credential reached the copilot's prompt — _run_bounded does not "
        "sanitize what the chat tools return"
    )


@pytest.mark.asyncio
async def test_chat_tool_error_text_is_sanitized(chat_context):
    """Exception messages can quote the failing input verbatim."""

    async def _boom(_state, _q):
        raise RuntimeError("upstream rejected " + LEAKED)

    out = await chat_read_tools._run_bounded("list_recent_runs", _boom)
    assert "hunter2xyz" not in out


def test_every_chat_tool_returns_through_the_sanitizing_path():
    """A chat tool that bypassed _run_bounded would skip sanitization."""
    import inspect

    for chat_tool in chat_read_tools.chat_tools():
        body = inspect.getsource(chat_tool.coroutine or chat_tool.func)
        assert "_run_bounded(" in body, (
            f"chat tool {chat_tool.name} does not return through _run_bounded, "
            "so its output reaches the copilot's prompt unsanitized"
        )
