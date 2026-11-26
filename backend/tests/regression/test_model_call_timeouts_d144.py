"""D-144: model calls outlast the pipeline's own limits.

* ``.env.example`` shipped ``AI_TIMEOUT_SECONDS=120`` while the code default
  is 300 (``Settings``), so a copied example halved the documented limit.
* One ``asyncio.wait_for(operation, timeout=llm_timeout)`` covered the small
  model's verdict AND the escalated explanation of the tiered root-cause path:
  two calls in sequence shared one call's timeout. Each call is bounded on its
  own by the wrapper (``BudgetedLLM`` stamps the per-call timeout), so the
  per-test guard now allows one timeout per call.
* A local model ran three analyses at once; on a processor they slow each other
  into the timeout. Unless the operator sets ``LLM_MAX_CONCURRENT_ANALYSES``,
  a local provider runs one at a time.

The tiered operation here makes its two calls through a REAL ``BudgetedLLM``
over a fake model on a virtual clock (``tests/virtual_clock``).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agents.analysis_agent import AnalysisAgent
from app.core.config import Settings, settings
from app.services import llm_circuit_breaker
from app.services import llm_cost_reservation as cost
from app.services.llm_factory import BudgetedLLM
from app.services.pipeline_ai_scope import build_ai_config_snapshot
from tests.virtual_clock import VirtualClockPolicy

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def event_loop_policy():
    return VirtualClockPolicy()


class _SlowModel(FakeListChatModel):
    """Answers after ``delay`` virtual seconds."""

    delay: float = 4.0
    calls: int = 0

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        await asyncio.sleep(self.delay)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])


@pytest.fixture
def gates(monkeypatch):
    async def _none(*_a, **_k):
        return None

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", _none)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(_none))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(_none))
    monkeypatch.setattr(cost, "reserve", _none)
    monkeypatch.setattr(cost, "settle", _none)


def _analysis_stage(monkeypatch, *, delay: float, per_call_timeout: int):
    """An AnalysisAgent whose tiered operation makes two calls in sequence
    (verdict, then escalation) through one real wrapper."""
    inner = _SlowModel(responses=["done"], delay=delay)
    llm = BudgetedLLM(inner, provider="openrouter", model="slow", default_timeout_seconds=per_call_timeout)
    done_at: list[float] = []

    async def _tiered(**_kwargs):
        await llm.ainvoke("verdict")      # the small model's verdict
        await llm.ainvoke("explanation")  # the escalated explanation
        done_at.append(asyncio.get_running_loop().time())
        return {
            "root_cause_summary": "The checkout service rounds the tax twice on every order.",
            "failure_category": "PRODUCT_BUG",
            "confidence_score": 90,
            "evidence_references": [{"source": "stacktrace"}],
            "tools_used": [],
            "_routing": {"mode_used": "llm", "mode_resolved": "llm", "fallback_from": None},
        }

    class _NoQuickAnswer:
        @staticmethod
        async def classify(**_kwargs):
            return None

    import app.services.analysis_router as router_module
    import app.services.training.classifier as fast_module

    monkeypatch.setattr(router_module, "classify_root_cause_tiered", _tiered)
    monkeypatch.setattr(fast_module, "FastClassifier", _NoQuickAnswer)
    monkeypatch.setattr("app.agents.analysis_agent.store_artifact", AsyncMock(return_value=None))

    stage = AnalysisAgent()
    stage.mark_stage_running = AsyncMock()  # type: ignore[method-assign]
    stage.mark_stage_done = AsyncMock()  # type: ignore[method-assign]
    stage.broadcast_progress = AsyncMock()  # type: ignore[method-assign]
    stage.log_decision = AsyncMock()  # type: ignore[method-assign]
    stage._fetch_human_corrections = AsyncMock(return_value={})  # type: ignore[method-assign]
    stage._resolve_adaptive_concurrency = AsyncMock(  # type: ignore[method-assign]
        return_value={"concurrency": 1, "rationale": "test"}
    )
    stage._batch_upsert_analyses = AsyncMock()  # type: ignore[method-assign]
    stage._record_latency_feedback = MagicMock()  # type: ignore[method-assign]
    stage._fetch_test_metadata = AsyncMock(  # type: ignore[method-assign]
        return_value={"tc-1": {"test_name": "test_tc-1", "severity": "NORMAL", "error_message": "xyzzy happened"}}
    )
    return stage, inner, done_at


def _state(*, timeout: int) -> dict:
    snapshot = build_ai_config_snapshot({"timeout_seconds": timeout, "timeout_seconds_source": "ai_config"})
    return {
        "pipeline_run_id": "pipeline-d144",
        "project_id": "symbolic-project",
        "failed_test_ids": ["tc-1"],
        "analysis_mode_requested": "llm",
        "analysis_mode_resolved": "llm",
        "analysis_mode_resolution": {"provider": "openrouter", "model": "slow"},
        "_cost_budget_prechecked": True,
        "_cost_budget_action": "ALLOW",
        "ai_config_snapshot": snapshot,
        "confidence_threshold": 80,
        "llm_timeout_seconds": snapshot["llm_timeout_seconds"],
        # The tiered path (a durable pipeline sets this from the project's
        # agent config); the operation itself is faked above.
        "_root_cause_tier_context": (object(), None, 5, None, None),
    }


@pytest.mark.asyncio
async def test_the_verdict_and_the_escalation_each_get_their_own_timeout(monkeypatch, gates):
    """Two 4 s calls under a 5 s per-call timeout: both answer (t=8). Before
    D-144 the one 5 s guard around both cut the escalation off at t=5."""
    stage, inner, done_at = _analysis_stage(monkeypatch, delay=4.0, per_call_timeout=5)

    result = await stage.run(_state(timeout=5))

    assert inner.calls == 2
    assert done_at == [pytest.approx(8.0)]
    audit = result["analyses"]["tc-1"]["_audit"]
    assert audit["fallback_from"] is None
    assert audit["analysis_mode"] == "llm"


@pytest.mark.asyncio
async def test_a_hung_call_is_still_cut_at_its_own_timeout(monkeypatch, gates):
    """Per call, not per pair: a first call that never answers is cut at 5 s
    by the wrapper (the quick classifier here declines at once)."""
    stage, inner, done_at = _analysis_stage(monkeypatch, delay=60.0, per_call_timeout=5)
    loop = asyncio.get_running_loop()

    result = await stage.run(_state(timeout=5))

    assert inner.calls == 1
    assert done_at == []
    assert loop.time() == pytest.approx(5.0)
    assert result["analyses"]["tc-1"]["_audit"]["fallback_from"] == "llm"


def test_the_tier_one_reason_says_the_quick_call_ran_after_the_limit():
    analysis = AnalysisAgent._record_llm_timeout_routing(
        {"fallback_tier": 1}, timeout=240, mode_requested="llm", mode_resolved="llm", quick_timeout=30,
    )
    assert analysis["_routing"]["fallback_reason"] == (
        "react_timeout: full investigation timed out after 240 s; "
        "a quick classification then answered (allowed 30 s more)"
    )


def _env_example_value(name: str) -> str:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    match = re.search(rf"^{name}=(\S*)", text, re.MULTILINE)
    assert match, f"{name} missing from .env.example"
    return match.group(1)


@pytest.mark.parametrize(
    "name", ["AI_TIMEOUT_SECONDS", "CHAT_FIRST_TOKEN_TIMEOUT_SECONDS", "CHAT_TURN_TIMEOUT_SECONDS"],
)
def test_env_example_timeouts_match_the_code_defaults(name):
    assert int(_env_example_value(name)) == Settings.model_fields[name].default


@pytest.fixture
def no_operator_concurrency(monkeypatch, tmp_path):
    """LLM_MAX_CONCURRENT_ANALYSES set in neither the environment nor an env
    file, whatever the machine running the tests has (order-independent: no
    ``settings`` attribute is assigned, so ``model_fields_set`` plays no part)."""
    import app.agents.analysis_agent as module

    monkeypatch.delenv("LLM_MAX_CONCURRENT_ANALYSES", raising=False)
    monkeypatch.setattr(module, "_ENV_FILES", (str(tmp_path / ".env"),))
    return tmp_path / ".env"


def _resolved(provider: str) -> dict:
    """A state whose FROZEN effective provider (Settings > AI over env) is ``provider``."""
    return {"analysis_mode_resolved": "llm", "analysis_mode_resolution": {"provider": provider, "model": "m"}}


@pytest.mark.asyncio
async def test_a_local_provider_runs_one_analysis_at_a_time_unless_the_operator_says(
    monkeypatch, no_operator_concurrency,
):
    # The env says the opposite of the stored choice every time (review finding 1).
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    policy = await AnalysisAgent()._resolve_adaptive_concurrency(_resolved("ollama"), 10)
    assert policy["concurrency"] == 1 and policy["provider"] == "ollama"

    no_operator_concurrency.write_text("LLM_MAX_CONCURRENT_ANALYSES=3\n", encoding="utf-8")
    policy = await AnalysisAgent()._resolve_adaptive_concurrency(_resolved("ollama"), 10)
    assert policy["concurrency"] == min(int(settings.LLM_MAX_CONCURRENT_ANALYSES), 3)

    no_operator_concurrency.unlink()
    monkeypatch.setenv("LLM_MAX_CONCURRENT_ANALYSES", "3")
    policy = await AnalysisAgent()._resolve_adaptive_concurrency(_resolved("ollama"), 10)
    assert policy["concurrency"] == min(int(settings.LLM_MAX_CONCURRENT_ANALYSES), 3)


@pytest.mark.asyncio
async def test_a_hosted_provider_chosen_in_settings_is_not_treated_as_local(monkeypatch, no_operator_concurrency):
    """Review finding 1: env ``ollama`` (the shipped default) with OpenAI chosen
    in Settings > AI ran one at a time; the frozen provider is OpenAI."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    policy = await AnalysisAgent()._resolve_adaptive_concurrency(_resolved("openai"), 10)
    base = max(1, int(settings.LLM_MAX_CONCURRENT_ANALYSES or 1))
    assert policy["provider"] == "openai"
    assert policy["concurrency"] == min(max(base, base * 2), 8)


def test_a_monkeypatched_setting_does_not_count_as_set_by_the_operator(monkeypatch, no_operator_concurrency):
    """Review finding 11: any assignment puts the name in ``model_fields_set``
    for good; the operator signal reads the environment and the env file."""
    import app.agents.analysis_agent as module

    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENT_ANALYSES", 2)
    assert "LLM_MAX_CONCURRENT_ANALYSES" in settings.model_fields_set
    assert module._concurrency_set_by_operator() is False
    monkeypatch.setenv("LLM_MAX_CONCURRENT_ANALYSES", "")  # empty is unset (env_ignore_empty)
    assert module._concurrency_set_by_operator() is False


def test_the_latency_feedback_is_keyed_by_the_effective_provider(monkeypatch):
    import app.agents.analysis_agent as module

    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(module, "_ANALYSIS_LATENCY_EWMA_BY_PROVIDER", {})
    AnalysisAgent()._record_latency_feedback(
        {"tc-1": {"_audit": {"analysis_duration_seconds": 30.0}}}, _resolved("openai"),
    )
    assert module._ANALYSIS_LATENCY_EWMA_BY_PROVIDER == {"openai": 30.0}


@pytest.mark.asyncio
async def test_a_test_started_near_the_deadline_gets_only_the_time_left(monkeypatch, gates):
    """Review finding 2: the doubled guard (2 x the per-call timeout) let a test
    started at t=1490 run to ~2120 while Celery hard-kills at 1800. Here 3 s are
    left: the two 2.5 s calls (5 s per call, 10 s pair) are cut at t=3, not 5."""
    import time as real_time

    stage, inner, done_at = _analysis_stage(monkeypatch, delay=2.5, per_call_timeout=5)
    loop = asyncio.get_running_loop()
    state = _state(timeout=5)
    state["pipeline_deadline_ts"] = real_time.monotonic() + 3.0

    result = await stage.run(state)

    assert inner.calls == 2
    assert done_at == []
    # The guard fired at ~3 s (the wall clock moved a little before the
    # stage read it), then the quick classifier declined at once.
    assert 2.0 < loop.time() <= 3.0
    assert result["analyses"]["tc-1"]["_audit"]["fallback_from"] == "llm"


@pytest.mark.asyncio
async def test_no_time_left_skips_the_quick_classification_and_never_goes_negative(monkeypatch, gates):
    """With the deadline almost reached the guard is the 1 s floor, and the
    quick model call is skipped rather than run past the deadline."""
    import app.agents.analysis_agent as module
    import app.services.training.classifier as fast_module

    stage, inner, _done_at = _analysis_stage(monkeypatch, delay=60.0, per_call_timeout=5)
    asked: list[str] = []

    class _CountingQuick:
        @staticmethod
        async def classify(**_kwargs):
            asked.append("quick")
            return None

    monkeypatch.setattr(fast_module, "FastClassifier", _CountingQuick)
    # A frozen deadline 0.5 s ahead on the stage's clock: the start checks pass,
    # the guard floors at 1 s, and no time is left for the quick call.
    monkeypatch.setattr(module, "_seconds_to_deadline", lambda state: 0.5)
    loop = asyncio.get_running_loop()

    result = await stage.run(_state(timeout=5))

    assert inner.calls == 1
    assert loop.time() == pytest.approx(module._MIN_OPERATION_SECONDS)
    assert asked == []
    assert result["analyses"]["tc-1"]["_audit"]["fallback_from"] == "llm"
