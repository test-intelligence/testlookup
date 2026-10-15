"""AIC-02: the Settings > AI "Timeout (seconds)" reaches every LLM call (G-C leaf half).

The value travels: stored ``ai_config.ai_timeout_seconds`` -> the effective
config -> ``get_llm`` stamps it on the ``BudgetedLLM`` it builds (inside a
pipeline: the value frozen at its start) -> every provider call through that
wrapper is bounded by it. Call sites with their own ``wait_for`` (summary,
release risk, watchman, chat compression, RAG generation, the ReAct executor)
compute theirs per run from the same rule instead of an import-time constant.

Every LLM here is the REAL ``BudgetedLLM`` over a ``FakeListChatModel`` whose
``_agenerate`` sleeps 10 s, on the virtual-clock loop (``tests/virtual_clock``):
"times out at 5, not 10" is read off ``loop.time()``, never the wall clock. The
breaker / cost reservation / cluster slot gates are switched to no-ops (they
need Redis and are covered by their own tests); the timed region is the real one.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.language_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.core import metrics
from app.core.config import settings
from app.services import llm_circuit_breaker
from app.services import llm_cost_reservation as cost
from app.services.llm_factory import BudgetedLLM, get_llm, live_llm_timeout_seconds
from app.services.pipeline_ai_scope import (
    build_ai_config_snapshot,
    effective_llm_timeout,
    pipeline_ai_scope,
)
from tests.virtual_clock import VirtualClockPolicy

PROVIDER = "ollama"


@pytest.fixture
def event_loop_policy():
    return VirtualClockPolicy()


class _SlowModel(FakeListChatModel):
    """Answers after ``delay`` virtual seconds (no thread pool: the loop's clock)."""

    delay: float = 10.0
    calls: int = 0

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        await asyncio.sleep(self.delay)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])

    def bind_tools(self, tools, **kwargs):
        """Tool binding, so ``bind_tools`` / ``with_structured_output`` can be derived."""
        return self.bind(**kwargs)


@pytest.fixture
def gates(monkeypatch):
    """Breaker, cost reservation and cluster slot off; record breaker failures."""
    failures: list[BaseException] = []

    async def _available(*_a, **_k):
        return None

    async def _record_failure(_provider, _base_url, exc):
        failures.append(exc)

    async def _record_success(*_a, **_k):
        return None

    async def _reserve(*_a, **_k):
        return None

    async def _settle(*_a, **_k):
        return None

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", _available)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(_record_failure))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(_record_success))
    monkeypatch.setattr(cost, "reserve", _reserve)
    monkeypatch.setattr(cost, "settle", _settle)
    return failures


def _stored(monkeypatch, timeout_seconds: Any, **extra) -> list[int]:
    """The effective config as the resolver returns it for a stored timeout.

    Returns a list that grows by one per resolver read.
    """
    from app.services import ai_config_resolver

    config = {
        "provider": PROVIDER,
        "model": "stored-model",
        "base_url": "http://127.0.0.1:11434",
        "temperature": 0.1,
        "max_tokens": 77,
        "offline_mode": False,
        "timeout_seconds": timeout_seconds,
        "timeout_seconds_source": "ai_config",
        **extra,
    }
    reads: list[int] = []

    async def _effective(*_a, **_k):
        reads.append(1)
        return dict(config)

    monkeypatch.setattr(ai_config_resolver, "get_effective_ai_config", _effective)
    return reads


def _timeouts() -> float:
    return metrics.llm_requests_total.labels(provider=PROVIDER, status="timeout")._value.get()


async def _llm_with_slow_inner(**get_llm_kwargs) -> tuple[BudgetedLLM, _SlowModel]:
    """``get_llm`` builds the real wrapper (and stamps the timeout); its provider
    client would dial a real Ollama, so the inner model is swapped for the slow
    fake AFTER construction: the wrapper, its stamp and its timed call are real."""
    llm = await get_llm(**get_llm_kwargs)
    assert isinstance(llm, BudgetedLLM)
    assert llm.provider_name == PROVIDER
    slow = _SlowModel(responses=["done"])
    llm._inner = slow
    return llm, slow


# -- 1. the stored value bounds the real wrapper's call -----------------------------------------


@pytest.mark.asyncio
async def test_a_stored_5s_timeout_stops_the_call_at_5_not_10(monkeypatch, gates):
    _stored(monkeypatch, 5)
    llm, slow = await _llm_with_slow_inner()
    assert llm.default_timeout_seconds == 5
    loop = asyncio.get_running_loop()
    before = _timeouts()
    started = loop.time()

    with pytest.raises(TimeoutError):
        await llm.ainvoke("summarise this run")

    assert loop.time() - started == pytest.approx(5.0)
    assert slow.calls == 1
    assert _timeouts() == before + 1, "observed once as status=timeout"
    assert len(gates) == 1 and isinstance(gates[0], TimeoutError), "the breaker counts it once"


@pytest.mark.asyncio
async def test_a_stored_300s_timeout_lets_the_10s_call_complete(monkeypatch, gates):
    _stored(monkeypatch, 300)
    llm, _slow = await _llm_with_slow_inner()
    loop = asyncio.get_running_loop()
    before = _timeouts()
    started = loop.time()

    result = await llm.ainvoke("summarise this run")

    assert result.content == "done"
    assert loop.time() - started == pytest.approx(10.0)
    assert _timeouts() == before
    assert gates == []


@pytest.mark.asyncio
async def test_inside_a_pipeline_the_frozen_value_wins_over_the_stored_one(monkeypatch, gates):
    _stored(monkeypatch, 300)
    snapshot = build_ai_config_snapshot({"timeout_seconds": 5, "timeout_seconds_source": "ai_config"}, {})
    with pipeline_ai_scope(snapshot):
        llm, _slow = await _llm_with_slow_inner()
        assert llm.default_timeout_seconds == 5
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(TimeoutError):
            await llm.ainvoke("x")
        assert loop.time() - started == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_derived_wrappers_keep_the_timeout(monkeypatch, gates):
    """bind / bind_tools / with_structured_output build new wrappers; one that
    forgot the timeout would be an unbounded call."""
    _stored(monkeypatch, 5)
    llm, _slow = await _llm_with_slow_inner()
    derived = [
        llm.bind(stop=["\n"]),
        llm.bind_tools([]),
        llm.with_structured_output({"title": "x", "type": "object", "properties": {"a": {"type": "string"}}}),
    ]
    for wrapper in derived:
        assert isinstance(wrapper, BudgetedLLM)
        assert wrapper.default_timeout_seconds == 5
    loop = asyncio.get_running_loop()
    for call in (derived[0].ainvoke, derived[1].ainvoke, llm.as_runnable().ainvoke):
        started = loop.time()
        with pytest.raises(TimeoutError):
            await call("x")
        assert loop.time() - started == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_a_stream_without_its_own_idle_bound_gets_the_timeout(monkeypatch, gates):
    class _SilentStream:
        max_tokens = 100

        def astream(self, *_a, **_k):
            async def _gen():
                await asyncio.sleep(10)
                yield AIMessage(content="late")

            return _gen()

    llm = BudgetedLLM(_SilentStream(), provider=PROVIDER, model="m", default_timeout_seconds=5)  # type: ignore[arg-type]
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(TimeoutError):
        async for _chunk in llm.astream("x"):
            pass
    assert loop.time() - started == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_a_hand_built_wrapper_stays_unbounded(gates):
    """No stamp, no bound: existing direct constructions keep their behaviour."""
    llm = BudgetedLLM(_SlowModel(responses=["done"]), provider=PROVIDER, model="m")
    assert llm.default_timeout_seconds is None
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert (await llm.ainvoke("x")).content == "done"
    assert loop.time() - started == pytest.approx(10.0)


# -- 5. an explicit provider (deviation, see the W04-G-C report) -------------------------------


@pytest.mark.asyncio
async def test_an_explicit_provider_is_bounded_without_a_config_read(monkeypatch, gates):
    """No app code passes ``provider=``: it is the resolver-free path tests and
    tools use. It keeps skipping the config read (without Redis each read costs
    seconds) and is still bounded: by the frozen value inside a pipeline, else
    by the env default."""
    reads = _stored(monkeypatch, 7)
    monkeypatch.setattr(settings, "AI_TIMEOUT_SECONDS", 42)
    llm = await get_llm(provider=PROVIDER)
    assert llm.default_timeout_seconds == 42
    with pipeline_ai_scope(build_ai_config_snapshot({"timeout_seconds": 9}, {})):
        assert (await get_llm(provider=PROVIDER)).default_timeout_seconds == 9
    assert reads == [], "the explicit-provider path must not read the config"
    # Selection is unchanged: the stored model / base URL are not applied.
    assert llm.model_label == settings.LLM_MODEL
    assert llm._base_url == settings.OLLAMA_BASE_URL


@pytest.mark.asyncio
async def test_get_llm_reads_the_config_once_and_stamps_it(monkeypatch, gates):
    reads = _stored(monkeypatch, 7)
    llm = await get_llm()
    assert llm.default_timeout_seconds == 7
    assert llm.model_label == "stored-model"
    assert reads == [1]


@pytest.mark.asyncio
async def test_an_unreadable_config_stamps_the_env_default(monkeypatch, gates):
    from app.services import ai_config_resolver

    async def _boom(*_a, **_k):
        raise RuntimeError("redis and postgres are down")

    monkeypatch.setattr(ai_config_resolver, "get_effective_ai_config", _boom)
    monkeypatch.setattr(settings, "AI_TIMEOUT_SECONDS", 42)
    monkeypatch.setattr(settings, "LLM_PROVIDER", PROVIDER)
    llm = await get_llm()
    assert llm.default_timeout_seconds == 42


def test_a_caller_holding_a_model_uses_its_stamp():
    """``llm_call_timeout`` (chat compression, the ReAct executor): the model's
    stamped value, capped, with no second config read; a test double gets the
    scope / env value."""
    from app.services.llm_factory import llm_call_timeout

    stamped = BudgetedLLM(_SlowModel(responses=["x"]), provider=PROVIDER, model="m", default_timeout_seconds=300)
    assert llm_call_timeout(stamped) == 300
    assert llm_call_timeout(stamped, cap=60) == 60
    short = BudgetedLLM(_SlowModel(responses=["x"]), provider=PROVIDER, model="m", default_timeout_seconds=5)
    assert llm_call_timeout(short, cap=60) == 5
    with pipeline_ai_scope(build_ai_config_snapshot({"timeout_seconds": 11}, {})):
        assert llm_call_timeout(object(), cap=60) == 11


# -- 3. the per-agent term and the site caps ---------------------------------------------------


def _frozen(agent_id: str, timeout_seconds: int, source: str = "project") -> dict:
    return {"agent_id": agent_id, "source": source, "config": {"agent_id": agent_id, "timeout_seconds": timeout_seconds}}


def test_an_explicit_project_override_tightens_the_global_value():
    from app.agents.summary_agent import summary_timeouts

    snapshot = build_ai_config_snapshot(
        {"timeout_seconds": 5}, {"agent.summary.v1": _frozen("agent.summary.v1", 3)}
    )
    state = {"llm_timeout_seconds": snapshot["llm_timeout_seconds"], "ai_config_snapshot": snapshot}
    assert effective_llm_timeout(state, agent_id="agent.summary.v1") == 3
    assert summary_timeouts(state) == (3, 2)


def test_a_stored_default_agent_row_does_not_lower_300_to_120():
    from app.services.agent_config_service import default_config

    agent_id = "agent.root_cause_analysis.v1"
    default = int(default_config(agent_id).timeout_seconds)
    assert default == 120
    snapshot = build_ai_config_snapshot({"timeout_seconds": 300}, {agent_id: _frozen(agent_id, default)})
    state = {"llm_timeout_seconds": 300, "ai_config_snapshot": snapshot}
    assert snapshot["agent_timeout_overrides"] == {}
    assert effective_llm_timeout(state, agent_id=agent_id) == 300


def test_the_site_caps():
    from app.agents.summary_agent import summary_timeouts

    assert summary_timeouts({"llm_timeout_seconds": 300}) == (120, 10)
    assert summary_timeouts({"llm_timeout_seconds": 5}) == (5, 2)
    assert summary_timeouts({"llm_timeout_seconds": 150}) == (120, 5)
    assert effective_llm_timeout({"llm_timeout_seconds": 300}, agent_id="agent.release_risk.v1", cap=90) == 90
    assert effective_llm_timeout({"llm_timeout_seconds": 30}, agent_id="agent.regression_watchman.v1", cap=90) == 30


# -- leaf sites, driven ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_summary_layers_use_the_runs_timeout(gates):
    from app.agents.summary_agent import SummaryAgent, SummaryLLMUnavailable, summary_timeouts

    slow = _SlowModel(responses=["done"])
    llm = BudgetedLLM(slow, provider=PROVIDER, model="m")  # the site's own wait_for is under test
    layer_timeout, _similar = summary_timeouts({"llm_timeout_seconds": 5})
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(SummaryLLMUnavailable, match="timed out after 5s"):
        await SummaryAgent()._generate_structured_report(
            run_data={"total_tests": 2, "failed_tests": 1, "passed_tests": 1, "pass_rate": 50.0},
            anomaly_summary="1 test failed",
            anomalies=[],
            analyses={"tc-1": {"failure_category": "PRODUCT_BUG", "root_cause_summary": "boom", "confidence_score": 80}},
            llm=llm,
            layer_timeout=layer_timeout,
        )
    # Layer 1 (5 s), then layers 2-4 concurrently, each a structured-output
    # attempt (5 s) and the prose call (5 s): 15 s. Every wait is the run's 5 s,
    # not the env default's 120 s cap (under which the 10 s model would answer).
    assert loop.time() - started == pytest.approx(15.0)
    assert slow.calls == 7


@pytest.mark.asyncio
async def test_the_watchman_classifies_with_the_frozen_timeout(monkeypatch, gates):
    import app.agents.regression_watchman as rwm

    slow = _SlowModel(responses=["{}"])
    llm = BudgetedLLM(slow, provider=PROVIDER, model="m")

    async def _get_llm(**_kwargs):
        return llm

    async def _no_branch(*_a, **_k):
        return None

    async def _no_history(*_a, **_k):
        return {}

    monkeypatch.setattr(rwm, "get_llm", _get_llm)
    agent = rwm.RegressionWatchman()
    monkeypatch.setattr(agent, "_resolve_run_branch", _no_branch)
    monkeypatch.setattr(agent, "_fetch_cluster_history", _no_history)
    state = {
        "test_run_id": "r-1",
        "project_id": "p-1",
        "llm_timeout_seconds": 5,
        "failure_clusters": [{"cluster_id": "cl_1", "member_test_ids": ["t1"], "label": "x"}],
        "analyses": {"t1": {"failure_category": "UNKNOWN", "error_message": "boom"}},
    }
    loop = asyncio.get_running_loop()
    started = loop.time()
    result = await agent._classify(state)
    assert slow.calls == 1, "the uncertain cluster went to the LLM"
    assert loop.time() - started == pytest.approx(5.0)
    assert "cl_1" in result, "the deterministic result stands after the timeout"


@pytest.mark.asyncio
async def test_release_risk_reasoning_uses_the_timeout_it_is_given(monkeypatch, gates):
    import app.agents.release_risk_agent as rra

    slow = _SlowModel(responses=["{}"])
    llm = BudgetedLLM(slow, provider=PROVIDER, model="m")

    async def _get_llm(**_kwargs):
        return llm

    monkeypatch.setattr(rra, "get_llm", _get_llm)
    timeout = effective_llm_timeout({"llm_timeout_seconds": 5}, agent_id="agent.release_risk.v1", cap=90)
    loop = asyncio.get_running_loop()
    started = loop.time()
    extras = await rra.ReleaseRiskAgent()._get_llm_reasoning(
        {"user_impact": 10}, 40.0, "CONDITIONAL_GO", "summary", {}, timeout=timeout,
    )
    assert loop.time() - started == pytest.approx(5.0)
    assert "deterministically" in extras["reasoning"]


# -- callers with no pipeline state: the live value ---------------------------------------------


@pytest.mark.asyncio
async def test_live_timeout_outside_a_pipeline_is_the_stored_value(monkeypatch):
    _stored(monkeypatch, 5)
    assert await live_llm_timeout_seconds() == 5
    _stored(monkeypatch, 300)
    assert await live_llm_timeout_seconds(cap=60) == 60


@pytest.mark.asyncio
async def test_live_timeout_inside_a_pipeline_is_the_frozen_value(monkeypatch):
    _stored(monkeypatch, 300)
    with pipeline_ai_scope(build_ai_config_snapshot({"timeout_seconds": 7}, {})):
        assert await live_llm_timeout_seconds() == 7
        assert await live_llm_timeout_seconds(cap=60) == 7


@pytest.mark.asyncio
async def test_rag_generation_times_out_at_the_stored_value(monkeypatch, gates):
    from app.services import rag_generation_service, test_case_ai_agent

    _stored(monkeypatch, 5)
    slow = _SlowModel(responses=["[]"])
    llm = BudgetedLLM(slow, provider=PROVIDER, model="m")

    async def _generate(prompt, *, project_id=None):
        await llm.ainvoke(prompt)
        return {"test_cases": [{"title": "late"}]}

    monkeypatch.setattr(test_case_ai_agent, "ai_generate_test_cases", _generate)
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(rag_generation_service.RagGenerationUnavailable, match="timed out"):
        await rag_generation_service._call_llm_generate("requirements", None)
    assert loop.time() - started == pytest.approx(5.0)
