"""Regression: the Ragas evaluator judges with the configured LLM, inside the cost cap.

Bug pinned (2026-10-15): ``_evaluate_via_ragas`` called
``ragas.evaluate(ds, metrics=[faithfulness])`` with no ``llm=``. Ragas then
built its own default judge, OpenAI from ``OPENAI_API_KEY``, whatever provider
Settings > AI named. Those calls skipped ``get_llm()``: no provider allowlist,
no reservation against the project's monthly LLM cap, no circuit breaker. The
``evaluate()`` docstring said so ("the cap cannot see those calls").

Handing Ragas the ``BudgetedLLM`` itself is not the fix either. Ragas's
``LangchainLLMWrapper`` calls ``agenerate_prompt`` on the model, which
``BudgetedLLM.__getattr__`` resolves to the inner provider model: two provider
calls, zero reservations (checked against ragas 0.4.3). ``as_chat_model()``
turns each generation into one ``BudgetedLLM.ainvoke``.

These tests drive the real resolver, ``get_llm()``, ``BudgetedLLM``, the
provider policy and the cost reservation. Stubbed: ``ragas`` (not installed in
CI; the stand-in calls the model the way ragas 0.4.3 does), the provider SDK
class, and the stores behind the breaker and the cap.
"""
from __future__ import annotations

import json
import os
import sys
import types
import uuid
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.prompt_values import StringPromptValue  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services import llm_circuit_breaker  # noqa: E402
from app.services import llm_cost_reservation as cost  # noqa: E402
from app.services import rag_faithfulness_service as svc  # noqa: E402
from app.services.llm_cost_reservation import Reservation  # noqa: E402

pytestmark = pytest.mark.regression

PROJECT = str(uuid.uuid4())
MODEL = "claude-haiku-4-5"
CASE = "Login rejects a wrong password and locks the account after 3 tries."
CITATIONS = ["Spec 4.2: a wrong password returns 401."]


# ── ragas stand-in ────────────────────────────────────────────────────────


class _RunConfig:
    def __init__(self, max_retries: int = 10, **_kw: Any) -> None:
        # ragas's default: 10 attempts, on any exception.
        self.max_retries = max_retries


class _SingleTurnSample:
    def __init__(self, *, user_input: str, response: str, retrieved_contexts: list[str]) -> None:
        self.user_input = user_input
        self.response = response
        self.retrieved_contexts = retrieved_contexts


class _LangchainLLMWrapper:
    """``ragas.llms.LangchainLLMWrapper`` as of 0.4.3, cut to the calls made.

    Sets ``temperature`` and ``n`` on the model when it has them, drives it
    through LangChain's ``agenerate_prompt``, and retries per its RunConfig.
    """

    def __init__(self, langchain_llm: Any, run_config: Optional[_RunConfig] = None) -> None:
        self.langchain_llm = langchain_llm
        self.run_config = run_config or _RunConfig()

    async def agenerate_text(self, prompt, n=1, temperature=0.01, stop=None, callbacks=None):
        if hasattr(self.langchain_llm, "temperature"):
            self.langchain_llm.temperature = temperature
        if hasattr(self.langchain_llm, "n"):
            self.langchain_llm.n = n
        return await self.langchain_llm.agenerate_prompt(
            prompts=[prompt] * n, stop=stop, callbacks=callbacks,
        )

    async def generate(self, prompt):
        for attempt in range(1, self.run_config.max_retries + 1):
            try:
                return await self.agenerate_text(prompt)
            except Exception:
                if attempt >= self.run_config.max_retries:
                    raise
        raise AssertionError("unreachable")


class _Faithfulness:
    """Two judge calls, as ragas makes: split into statements, then a verdict each."""

    def __init__(self, llm: Any = None) -> None:
        self.llm = llm

    async def single_turn_ascore(self, sample: _SingleTurnSample) -> float:
        assert self.llm is not None, "llm must be set to compute score"
        await self.llm.generate(StringPromptValue(text=f"STATEMENTS for: {sample.response}"))
        result = await self.llm.generate(StringPromptValue(
            text="VERDICTS against: " + "\n".join(sample.retrieved_contexts)
        ))
        verdicts = json.loads(result.generations[0][0].text)["statements"]
        return sum(v["verdict"] for v in verdicts) / len(verdicts) if verdicts else float("nan")


@pytest.fixture
def stub_ragas(monkeypatch):
    modules = {
        "ragas": types.ModuleType("ragas"),
        "ragas.dataset_schema": types.ModuleType("ragas.dataset_schema"),
        "ragas.llms": types.ModuleType("ragas.llms"),
        "ragas.metrics": types.ModuleType("ragas.metrics"),
        "ragas.run_config": types.ModuleType("ragas.run_config"),
    }
    modules["ragas.dataset_schema"].SingleTurnSample = _SingleTurnSample
    modules["ragas.llms"].LangchainLLMWrapper = _LangchainLLMWrapper
    modules["ragas.metrics"].Faithfulness = _Faithfulness
    modules["ragas.run_config"].RunConfig = _RunConfig
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(svc, "ragas_installed", lambda: True)


# ── configured provider, gates ────────────────────────────────────────────


class _SessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def configured_anthropic(monkeypatch, fake_redis):
    """Settings > AI names Anthropic; the env names Ollama and holds an OpenAI key.

    Returns the list of prompts the Anthropic model received. ``ChatOpenAI``
    fails the test if anything builds it.
    """
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "AI_LLM_PROVIDER_ALLOWLIST", "")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai-must-not-be-used")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-must-not-be-used")
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake_redis)

    async def _no_secret(_db, _scope, _key):
        return None

    monkeypatch.setattr("app.services.secret_service.read_secret", _no_secret)
    row = MagicMock()
    row.scalar_one_or_none = MagicMock(return_value=MagicMock(value={
        "llm_provider": "anthropic",
        "llm_model": MODEL,
        "rag_faithfulness_evaluator": "ragas",
    }))
    db = AsyncMock()
    db.execute = AsyncMock(return_value=row)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", MagicMock(return_value=_SessionCtx(db)))
    monkeypatch.setattr(svc, "_feature_enabled", AsyncMock(return_value=True))

    prompts: list[str] = []

    class FakeChatAnthropic(BaseChatModel):
        model: str = ""
        api_key: Any = None
        temperature: Optional[float] = None
        max_tokens: Optional[int] = None
        max_retries: int = 0

        @property
        def _llm_type(self) -> str:
            return "fake-anthropic"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise AssertionError("the judge must not call the provider synchronously")

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            text = "\n".join(str(m.content) for m in messages)
            prompts.append(text)
            if text.startswith("VERDICTS"):
                body: dict = {"statements": [
                    {"statement": "A wrong password is rejected.", "verdict": 1},
                    {"statement": "The account locks after 3 tries.", "verdict": 0},
                ]}
            elif text.startswith("STATEMENTS"):
                body = {"statements": ["A wrong password is rejected.", "The account locks after 3 tries."]}
            else:  # the single-prompt judge
                body = {"score": 0.9, "reason": "single-prompt judge"}
            message = AIMessage(
                content=json.dumps(body),
                usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

    class ChatOpenAIMustNotBeBuilt:
        def __init__(self, *_a, **_k):
            raise AssertionError("an OpenAI judge was built; Settings > AI names Anthropic")

    # get_llm imports the SDK inside its branch, so a stand-in module is enough.
    anthropic_sdk = types.ModuleType("langchain_anthropic")
    anthropic_sdk.ChatAnthropic = FakeChatAnthropic
    monkeypatch.setitem(sys.modules, "langchain_anthropic", anthropic_sdk)
    monkeypatch.setattr("langchain_openai.ChatOpenAI", ChatOpenAIMustNotBeBuilt)
    return prompts


@pytest.fixture
def open_breaker(monkeypatch):
    async def _available(*_a, **_k):
        return None

    async def _record(*_a, **_k):
        return None

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", _available)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(_record))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(_record))


@pytest.fixture
def reservations(monkeypatch, open_breaker):
    """Every reservation, with the project scope in force when it was made."""
    seen: list[tuple[str, str, Optional[str]]] = []

    async def _reserve(provider, model, *, input_tokens, max_output_tokens):
        seen.append((provider, model, cost.current_cost_scope()))
        return Reservation(key="k", project_id=PROJECT, estimated_usd=0.01, ttl_seconds=60)

    async def _settle(*_a, **_k):
        return None

    monkeypatch.setattr(cost, "reserve", _reserve)
    monkeypatch.setattr(cost, "settle", _settle)
    return seen


# ── tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ragas_judges_with_the_configured_provider_inside_the_cost_cap(
    stub_ragas, configured_anthropic, reservations
):
    result = await svc.evaluate(CASE, CITATIONS, project_id=PROJECT)

    assert result == {
        "score": 0.5,
        "evaluator": "ragas",
        "reason": "ragas faithfulness metric (sample size 1 citations)",
    }
    # Both ragas calls reached the provider Settings > AI names...
    assert [p.split(" ", 1)[0] for p in configured_anthropic] == ["STATEMENTS", "VERDICTS"]
    # ...and each was reserved against this project's cap first.
    assert reservations == [("anthropic", MODEL, PROJECT)] * 2


@pytest.mark.asyncio
async def test_a_cost_cap_refusal_stops_the_ragas_judge(
    stub_ragas, configured_anthropic, open_breaker, monkeypatch
):
    attempts: list[Optional[str]] = []
    real_reserve = cost.reserve

    async def _counting_reserve(*args, **kwargs):
        attempts.append(cost.current_cost_scope())
        return await real_reserve(*args, **kwargs)

    async def _cap_spent(_project_id):
        return (1.0, 1.0)  # hard cap $1, $1 already committed this month

    class _RefusingStore:
        async def eval(self, *_args):
            return [0, "1.0"]

    monkeypatch.setattr(cost, "reserve", _counting_reserve)
    monkeypatch.setattr(cost, "_cap_context", _cap_spent)
    monkeypatch.setattr(cost, "_redis", lambda: _RefusingStore())

    result = await svc.evaluate(CASE, CITATIONS, project_id=PROJECT)

    assert result is not None
    assert result["score"] == 0.0
    assert result["evaluator"] == "ragas"
    assert "LLM cost budget" in result["reason"]
    assert configured_anthropic == []  # no request was sent
    # One attempt: ragas's own retry (10 by default) would ask the cap again.
    assert attempts == [PROJECT]


@pytest.mark.asyncio
async def test_the_provider_allowlist_applies_to_the_ragas_judge(
    stub_ragas, configured_anthropic, reservations, monkeypatch
):
    monkeypatch.setattr(settings, "AI_LLM_PROVIDER_ALLOWLIST", "ollama")

    result = await svc.evaluate(CASE, CITATIONS, project_id=PROJECT)

    assert result is not None
    assert result["score"] == 0.0
    assert "AI_LLM_PROVIDER_ALLOWLIST" in result["reason"]
    assert configured_anthropic == []
    assert reservations == []


@pytest.mark.asyncio
async def test_ragas_telemetry_is_switched_off(stub_ragas, configured_anthropic, reservations, monkeypatch):
    # Ragas posts usage events to its vendor unless this is "true".
    monkeypatch.setenv("RAGAS_DO_NOT_TRACK", "false")

    await svc.evaluate(CASE, CITATIONS, project_id=PROJECT)

    assert os.environ["RAGAS_DO_NOT_TRACK"] == "true"


@pytest.mark.asyncio
async def test_ragas_nan_score_fails_closed(stub_ragas, configured_anthropic, reservations, monkeypatch):
    # A failed ragas judgement is NaN; clamped, it would read as 1.0 and pass.
    async def _nan(self, sample):
        return float("nan")

    monkeypatch.setattr(_Faithfulness, "single_turn_ascore", _nan)

    score, reason = await svc._evaluate_via_ragas(CASE, CITATIONS)

    assert score == 0.0
    assert "no faithfulness score" in reason


@pytest.mark.asyncio
async def test_unimportable_ragas_runs_the_single_prompt_judge_and_says_so(
    configured_anthropic, reservations, monkeypatch
):
    # Found by ``ragas_installed`` (find_spec) but broken at import, e.g. a
    # dependency at the wrong version.
    monkeypatch.setattr(svc, "ragas_installed", lambda: True)
    monkeypatch.setitem(sys.modules, "ragas", None)
    monkeypatch.setitem(sys.modules, "ragas.dataset_schema", None)

    result = await svc.evaluate(CASE, CITATIONS, project_id=PROJECT)

    # The label names the provider that judged, not "ragas".
    assert result == {"score": 0.9, "evaluator": "anthropic", "reason": "single-prompt judge"}
    assert reservations == [("anthropic", MODEL, PROJECT)]
