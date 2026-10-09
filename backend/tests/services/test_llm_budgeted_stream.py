"""BudgetedLLM.astream / bind_tools: streaming and tool binding stay inside the gates.

Regression (Ask-AI chat, 2026-10-09): the chat's tool loop handed BudgetedLLM to
LangChain's create_react_agent, which rejected it ("Expected a Runnable") on
every message for as long as the loop existed. Its tests mocked ``get_llm``
with a raw model, so the wrapper the deployment actually returns was never in
the path. These tests drive the REAL wrapper.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage

from app.services import llm_circuit_breaker, llm_cost_reservation as cost
from app.services.llm_cost_reservation import Reservation
from app.services.llm_factory import BudgetedLLM, tools_unsupported_error

PROJECT = str(uuid.uuid4())


class _StreamInner:
    """A provider client that streams scripted chunks (or fails)."""

    max_tokens = 2000

    def __init__(self, pieces=("Hel", "lo"), *, fail=None, fail_after=None, delay=0.0, usage=(120, 7)):
        self.pieces = list(pieces)
        self.fail = fail
        self.fail_after = fail_after
        self.delay = delay
        self.usage = usage
        self.calls: list[dict] = []
        self.closed = 0
        self.kwargs: dict = {}

    def astream(self, *args, **kwargs):
        self.calls.append(kwargs)
        return self._gen()

    async def _gen(self):
        try:
            if self.fail is not None and self.fail_after is None:
                raise self.fail
            for i, piece in enumerate(self.pieces):
                if self.delay:
                    await asyncio.sleep(self.delay)
                if self.fail is not None and self.fail_after == i:
                    raise self.fail
                last = i == len(self.pieces) - 1
                yield AIMessageChunk(
                    content=piece,
                    usage_metadata=(
                        {"input_tokens": self.usage[0], "output_tokens": self.usage[1],
                         "total_tokens": sum(self.usage)}
                        if last and self.usage else None
                    ),
                )
        finally:
            self.closed += 1

    def bind_tools(self, tools, **kwargs):
        bound = _StreamInner(self.pieces, usage=self.usage)
        bound.kwargs = {"tools": [getattr(t, "name", str(t)) for t in tools], **kwargs}
        return bound


class _Ledger(list):
    def __init__(self):
        super().__init__()
        self.meter: list[bool] = []


@pytest.fixture
def gates(monkeypatch):
    """Breaker open-check, cluster slots off; reserve/settle and breaker
    outcomes recorded instead of stored."""
    from app.core.config import settings

    events = _Ledger()
    breaker = {"failure": [], "success": 0}

    async def available(*_a, **_k):
        return None

    async def fake_reserve(provider, model, *, input_tokens, max_output_tokens):
        events.append(("reserve", provider, model, input_tokens, max_output_tokens))
        return Reservation(key="k", project_id=PROJECT, estimated_usd=0.5, ttl_seconds=60)

    async def fake_settle(reservation, actual, *, record_meter=False, **tokens):
        events.append(("settle", actual, tokens))
        events.meter.append(record_meter)

    async def record_failure(provider, base_url, exc):
        breaker["failure"].append(exc)

    async def record_success(provider, base_url):
        breaker["success"] += 1

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", available)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(record_failure))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(record_success))
    monkeypatch.setattr(cost, "reserve", fake_reserve)
    monkeypatch.setattr(cost, "settle", fake_settle)
    events.breaker = breaker  # type: ignore[attr-defined]
    return events


def _llm(inner, provider="openrouter", model="mistralai/mistral-nemo"):
    return BudgetedLLM(inner, provider=provider, model=model)


async def _collect(stream):
    return [chunk async for chunk in stream]


@pytest.mark.asyncio
async def test_stream_yields_chunks_and_settles_on_reported_usage(gates):
    inner = _StreamInner(("Hel", "lo"), usage=(120, 7))
    chunks = await _collect(_llm(inner).astream([HumanMessage(content="hi")]))

    assert "".join(c.content for c in chunks) == "Hello"
    assert [e[0] for e in gates] == ["reserve", "settle"]
    settled = gates[-1]
    # Priced at the usage the provider reported, not the reserved worst case.
    assert settled[1] != 0.5
    assert settled[2] == {"input_tokens": 120, "output_tokens": 7}
    assert gates.breaker["success"] == 1 and gates.breaker["failure"] == []
    assert inner.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,asked", [
    ("openrouter", True), ("openai", True), ("ollama", False), ("lmstudio", False), ("vllm", False),
])
async def test_usage_is_requested_only_from_providers_that_take_the_flag(gates, provider, asked):
    inner = _StreamInner(("x",))
    await _collect(_llm(inner, provider=provider).astream([HumanMessage(content="hi")]))
    assert ("stream_usage" in inner.calls[0]) is asked


@pytest.mark.asyncio
async def test_a_reader_that_stops_early_keeps_the_worst_case_and_blames_no_provider(gates):
    inner = _StreamInner(("a", "b", "c", "d"))
    stream = _llm(inner).astream([HumanMessage(content="hi")])
    first = await stream.__anext__()
    assert first.content == "a"
    await stream.aclose()

    assert gates[-1][0] == "settle" and gates[-1][1] == 0.5  # reserved worst case
    assert gates.meter[-1] is True
    assert gates.breaker["failure"] == []  # a Stop is not an outage
    assert inner.closed == 1  # the provider stream was released


@pytest.mark.asyncio
async def test_a_stalled_provider_times_out_through_the_failure_path(gates):
    inner = _StreamInner(("a", "b"), delay=0.5)
    with pytest.raises(TimeoutError):
        await _collect(_llm(inner).astream([HumanMessage(content="hi")], idle_timeout=0.05))

    # Counted against the breaker: a provider that hangs every call must get
    # its circuit opened, not be waited on by each chat turn.
    assert len(gates.breaker["failure"]) == 1
    assert isinstance(gates.breaker["failure"][0], TimeoutError)
    assert gates[-1][1] == 0.5


@pytest.mark.asyncio
async def test_the_idle_timer_covers_gaps_not_the_whole_stream(gates):
    # Each gap (0.03 s) is under the idle limit (0.2 s) although the whole
    # stream (0.24 s) is over it.
    inner = _StreamInner(tuple("abcdefgh"), delay=0.03)
    chunks = await _collect(_llm(inner).astream([HumanMessage(content="hi")], idle_timeout=0.2))
    assert len(chunks) == 8


class _ProviderError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.asyncio
async def test_a_refusal_of_tools_is_not_counted_against_the_breaker(gates):
    refusal = _ProviderError("No endpoints found that support tool use", 404)
    inner = _StreamInner(fail=refusal)
    with pytest.raises(_ProviderError):
        await _collect(_llm(inner).astream([HumanMessage(content="hi")]))
    assert gates.breaker["failure"] == []


@pytest.mark.asyncio
async def test_a_server_error_is_counted_against_the_breaker(gates):
    inner = _StreamInner(fail=_ProviderError("upstream error", 502))
    with pytest.raises(_ProviderError):
        await _collect(_llm(inner).astream([HumanMessage(content="hi")]))
    assert len(gates.breaker["failure"]) == 1


@pytest.mark.asyncio
async def test_a_failure_after_text_is_not_retried(gates, monkeypatch):
    import httpx

    llm = BudgetedLLM(
        _StreamInner(("a", "b"), fail=httpx.ConnectError("reset"), fail_after=1),
        provider="ollama", model="m", connect_retries=3,
    )
    with pytest.raises(httpx.ConnectError):
        await _collect(llm.astream([HumanMessage(content="hi")]))


def test_bind_tools_stays_inside_the_wrapper():
    llm = _llm(_StreamInner())
    bound = llm.bind_tools([type("T", (), {"name": "list_recent_runs"})()], tool_choice="required")
    assert isinstance(bound, BudgetedLLM)
    assert bound.provider_name == "openrouter"
    assert bound.model_label == "mistralai/mistral-nemo"
    assert bound._inner.kwargs["tool_choice"] == "required"


@pytest.mark.asyncio
async def test_bound_tool_schemas_count_toward_the_reservation(gates):
    plain = _llm(_StreamInner(("x",)))
    await _collect(plain.astream([HumanMessage(content="hi")]))
    bound = plain.bind_tools([type("T", (), {"name": "a_tool_with_a_long_schema_name" * 20})()])
    await _collect(bound.astream([HumanMessage(content="hi")]))
    reserves = [e for e in gates if e[0] == "reserve"]
    assert reserves[1][3] > reserves[0][3]


@pytest.mark.parametrize("message,code,expected", [
    ("No endpoints found that support tool use", 404, True),
    ("registry.ollama.ai/library/llama3 does not support tools", 400, True),
    ("Not found", 404, False),
    ("internal error", 500, False),
    ("tool use is unsupported for this model", None, True),
])
def test_tools_unsupported_error(message, code, expected):
    assert tools_unsupported_error(_ProviderError(message, code)) is expected
