"""QA-B45-R3-1: an SDK retry inside a WRAPPED client is still seen.

``with_structured_output`` returns a RunnableSequence (a RunnableBinding
over ChatOpenAI, then a parser). The retry check read only the top object and
its ``bound``, found no ``max_retries``, and refunded a structured call the
SDK had retried: attempt 1 sent and billed (read timeout), attempt 2 refused
to connect. The summary agent uses this path on every structured layer.

Real ChatOpenAI, real langchain wrappers, httpx.MockTransport; reserve and
settle are recorded, not stored.
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import BaseModel

from app.services import llm_cost_reservation as cost
from app.services.llm_cost_reservation import Reservation, cost_budget_scope
from app.services.llm_factory import BudgetedLLM, _internal_retries

httpx = pytest.importorskip("httpx")
pytest.importorskip("langchain_openai")
from langchain_openai import ChatOpenAI  # noqa: E402

PROJECT = str(uuid.uuid4())


class Verdict(BaseModel):
    verdict: str


@pytest.fixture(autouse=True)
def no_cluster_bound(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)


@pytest.fixture
def settled(monkeypatch):
    events: list[tuple[float, bool]] = []

    async def fake_reserve(provider, model, *, input_tokens, max_output_tokens):
        return Reservation(key="k", project_id=PROJECT, estimated_usd=1.0, ttl_seconds=60)

    async def fake_settle(reservation, actual, *, record_meter=False, **tokens):
        events.append((actual, record_meter))

    monkeypatch.setattr(cost, "reserve", fake_reserve)
    monkeypatch.setattr(cost, "settle", fake_settle)
    return events


def _chat(sent: list, max_retries: int) -> tuple[ChatOpenAI, "httpx.AsyncClient"]:
    def provider(request):
        sent.append(1)
        if len(sent) % 2 == 1:
            raise httpx.ReadTimeout("generated and billed, then the read timed out", request=request)
        raise httpx.ConnectError("the retry could not connect", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    chat = ChatOpenAI(
        model="gpt-4o-mini", api_key="sk-test", base_url="http://provider.invalid/v1",
        max_retries=max_retries, max_tokens=100, http_async_client=client,
    )
    return chat, client


def _shape(name: str, chat: ChatOpenAI) -> BudgetedLLM:
    llm = BudgetedLLM(chat, provider="openai", model="gpt-4o-mini")
    if name == "plain":
        return llm
    if name == "bind":
        return llm.bind(stop=["\n\n"])
    if name == "structured":
        return llm.with_structured_output(Verdict)
    if name == "structured-json-mode":
        return llm.with_structured_output(Verdict, method="json_mode")
    raise AssertionError(name)


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["plain", "bind", "structured", "structured-json-mode"])
async def test_an_sdk_retried_call_is_charged_whatever_wraps_the_model(settled, shape):
    sent: list = []
    chat, client = _chat(sent, max_retries=1)
    try:
        with cost_budget_scope(PROJECT):
            with pytest.raises(Exception):
                await _shape(shape, chat).ainvoke("prompt")
    finally:
        await client.aclose()
    assert len(sent) == 2, (shape, sent)  # the SDK retried inside the one call
    assert settled == [(1.0, True)], shape  # charged and metered, not refunded


@pytest.mark.asyncio
async def test_a_langchain_with_retry_wrapper_counts_as_retrying(settled):
    """``.with_retry`` is a RunnableRetry: it re-sends inside the one call too."""
    sent: list = []
    chat, client = _chat(sent, max_retries=0)
    llm = BudgetedLLM(chat.with_retry(stop_after_attempt=2), provider="openai", model="gpt-4o-mini")
    try:
        with cost_budget_scope(PROJECT):
            with pytest.raises(Exception):
                await llm.ainvoke("prompt")
    finally:
        await client.aclose()
    assert len(sent) == 2
    assert settled == [(1.0, True)]


@pytest.mark.asyncio
async def test_a_wrapped_client_with_retries_off_is_still_refunded_on_a_connect_error(settled):
    sent: list = []

    def refuses(request):
        sent.append(1)
        raise httpx.ConnectError("refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuses))
    chat = ChatOpenAI(
        model="gpt-4o-mini", api_key="sk-test", base_url="http://provider.invalid/v1",
        max_retries=0, max_tokens=100, http_async_client=client,
    )
    try:
        with cost_budget_scope(PROJECT):
            with pytest.raises(Exception):
                await _shape("structured", chat).ainvoke("prompt")
    finally:
        await client.aclose()
    assert len(sent) == 1
    assert settled == [(0.0, False)]


def test_the_retry_count_is_found_through_every_wrapper():
    chat = ChatOpenAI(model="gpt-4o-mini", api_key="sk-test", max_retries=3)
    assert _internal_retries(chat, set()) == 3
    assert _internal_retries(chat.bind(stop=["x"]), set()) == 3
    assert _internal_retries(chat.with_structured_output(Verdict), set()) == 3
    assert _internal_retries(chat.with_retry(stop_after_attempt=4), set()) == 3
    zero = ChatOpenAI(model="gpt-4o-mini", api_key="sk-test", max_retries=0)
    assert _internal_retries(zero.with_structured_output(Verdict), set()) == 0
    assert _internal_retries(zero.with_retry(stop_after_attempt=4), set()) == 3
    assert _internal_retries(object(), set()) == 0
