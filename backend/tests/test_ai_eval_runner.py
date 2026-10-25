"""W16 AGT-09 (b): the candidate replay runner (``services/ai_eval_runner.py``).

Every engine here is the REAL one: ``analysis_router.classify_test`` (rules /
ML / the ReAct triage), the duplicate detector's ratio, the release policy.
The LLM tier drives the real ``BudgetedLLM`` wrapper around a scripted chat
model returning the RECORDED responses (``fixtures/ai_eval_replay``) -- never
a mocked ``get_llm`` that skips the wrapper (a crash once hid behind one).
Only the stores the wrapper and the triage write to are stubbed (circuit
breaker, cost reservation, the Mongo audit trail, the event log).
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from langchain_core.language_models import FakeListChatModel

import app.services.agent as triage
from app.core.config import settings
from app.models.postgres import ReleaseGatePolicy
from app.models.schemas import EvalCandidateIn, EvalCandidateResolved
from app.services import ai_eval_runner as runner
from app.services import llm_circuit_breaker, llm_cost_reservation as cost
from app.services.ai_eval_vocab import current_prompt_tag
from app.services.golden_datasets import GOLDEN_DATASETS
from app.services.llm_cost_reservation import Reservation
from app.services.llm_factory import BudgetedLLM
from app.services.pipeline_ai_scope import build_ai_config_snapshot
from app.services.step_llm_budget import StepLLMBudget

PROJECT = "00000000-0000-0000-0000-0000000000a1"
RECORDINGS = Path(__file__).parent / "fixtures" / "ai_eval_replay" / "recordings.json"


def _candidate(tier: str, **resolved) -> EvalCandidateResolved:
    return EvalCandidateResolved(tier=tier, offline_mode=False, requested=EvalCandidateIn(tier=tier), **resolved)


def _golden(task_type: str) -> list[dict]:
    return GOLDEN_DATASETS[task_type]["get_items"]()


async def _replay(task_type, item, index, tier="rules", *, budget=None, timeout_s=30.0,
                  policy_context=None, scope=None):
    return await runner.replay_item(
        task_type=task_type, item=item, index=index, candidate=_candidate(tier), project_id=PROJECT,
        policy_context=policy_context, budget=budget or StepLLMBudget(limit=50), timeout_s=timeout_s,
        run_id="fpA1b2C3d4", ai_scope=scope or build_ai_config_snapshot({}),
    )


# ── rules / ML through the real router ─────────────────────────────────────


@pytest.mark.asyncio
async def test_rules_tier_replays_through_the_real_classify_test():
    items = _golden("classification")
    outcomes = [await _replay("classification", item, i) for i, item in enumerate(items)]

    assert {o.mode_used for o in outcomes} == {"rules"}
    assert all(o.output["mode_used"] == "rules" for o in outcomes)
    statuses = [o.status for o in outcomes]
    assert set(statuses) <= {"correct", "incorrect"}
    # The golden inputs hold the label as a "prediction" (input.failure_category);
    # a replay that read it would score 12 / 12. The rules engine does not.
    assert statuses.count("correct") == 7
    by_label = {item["metadata"]["label"]: o for item, o in zip(items, outcomes)}
    assert by_label["clear_product_bug_npe"].output["failure_category"] == "AUTOMATION_DEFECT"
    assert by_label["clear_product_bug_npe"].status == "incorrect"


def test_the_replayed_test_case_carries_text_only():
    item = _golden("classification")[0]
    case = runner.replay_test_case("fpA1b2C3d4", 3, item["input"], item["metadata"])
    assert case == {
        "test_case_id": "ai-eval:fpA1b2C3d4:3",
        "test_name": "clear_product_bug_npe",
        "error_message": item["input"]["root_cause_summary"],
        "stack_trace": None,
    }
    # Never the leaked label, never a project / run / hints: no cache key, no tool context.
    for absent in ("failure_category", "confidence_score", "is_flaky", "project_id", "run_id"):
        assert absent not in case


@pytest.mark.asyncio
async def test_ml_tier_without_a_trained_model_is_a_tier_fallback_error(monkeypatch, tmp_path):
    from app.services.ml import classifier

    monkeypatch.setattr(settings, "ML_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(classifier, "_model", None)
    outcome = await _replay("classification", _golden("classification")[3], 3, tier="ml")

    assert outcome.status == "error"
    assert outcome.error_code == "tier_fallback"
    assert outcome.error_text == "The ml engine was unavailable; rules answered instead, so the item is not scored."
    assert outcome.output["mode_used"] == "rules"
    assert outcome.mode_used == "rules"


@pytest.mark.asyncio
async def test_ml_tier_with_a_trained_model_is_scored_as_ml(monkeypatch, tmp_path):
    joblib = pytest.importorskip("joblib")
    linear = pytest.importorskip("sklearn.linear_model")
    from app.services.ml import classifier
    from app.services.ml.feature_extractor import FEATURE_NAMES

    width = len(FEATURE_NAMES)
    rows = [[float((i * 7 + j) % 5) for j in range(width)] for i in range(12)]
    model = linear.LogisticRegression(max_iter=200).fit(rows, [i % 6 for i in range(12)])
    joblib.dump(model, tmp_path / "classifier_v1.joblib")
    monkeypatch.setattr(settings, "ML_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(classifier, "_model", None)

    outcome = await _replay("classification", _golden("classification")[0], 0, tier="ml")

    assert outcome.mode_used == "ml"
    assert outcome.output["mode_used"] == "ml"
    assert outcome.status in ("correct", "incorrect")


# ── the LLM tier: the real BudgetedLLM around the recorded responses ──────


def _recordings() -> dict:
    return json.loads(RECORDINGS.read_text(encoding="utf-8"))


@pytest.fixture
def gates(monkeypatch):
    """The wrapper's and the triage's stores stubbed; the wrapper itself is real."""
    calls: dict = {"reserve": 0, "scopes": [], "touched": [], "holder": {}}

    async def available(*_a, **_k):
        return None

    async def reserve(provider, model, *, input_tokens, max_output_tokens):
        calls["reserve"] += 1
        calls["scopes"].append(cost.current_cost_scope())
        return Reservation(key="k", project_id=PROJECT, estimated_usd=0.01, ttl_seconds=60)

    async def settle(*_a, **_k):
        return None

    async def record_failure(provider, base_url, exc):
        return None

    async def record_success(provider, base_url):
        return None

    async def get_llm(*_a, **_k):
        return calls["holder"]["llm"]

    def forbidden(name):
        def _touch(*_a, **_k):
            calls["touched"].append(name)
            raise RuntimeError(f"{name} reached")
        return _touch

    from app.services.model_registry import ModelRegistry
    from app.services.training import classifier as fast_classifier

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(settings, "CLASSIFIER_CONFIDENCE_THRESHOLD", 85)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", available)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(record_failure))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(record_success))
    monkeypatch.setattr(cost, "reserve", reserve)
    monkeypatch.setattr(cost, "settle", settle)
    monkeypatch.setattr(ModelRegistry, "get_active_model", AsyncMock(return_value=None))
    monkeypatch.setattr(triage, "get_llm", get_llm)
    monkeypatch.setattr(fast_classifier, "get_llm", get_llm)
    calls["audit"] = AsyncMock()
    monkeypatch.setattr(triage, "_store_audit_trail", calls["audit"])
    monkeypatch.setattr(triage, "_emit_event", AsyncMock())
    # Project data a ReAct tool could reach: any touch is recorded (a tool
    # turns an exception into an observation, so raising alone proves nothing).
    monkeypatch.setattr("app.tools.check_flakiness.AsyncSessionLocal", forbidden("postgres"))
    monkeypatch.setattr("app.tools.fetch_stacktrace.get_mongo_db", forbidden("mongo_stacktrace"))
    monkeypatch.setattr("app.tools.fetch_rest_payload.get_mongo_db", forbidden("mongo_payload"))
    monkeypatch.setattr("app.services.memory_recall.recall_failure_history", forbidden("recall"))
    monkeypatch.setattr(
        "app.services.integration_config_service.runtime_integrations_for", forbidden("integrations")
    )
    return calls


def _scripted(calls: dict, responses: list[str], *, model=None) -> None:
    inner = model or FakeListChatModel(responses=responses)
    calls["holder"]["llm"] = BudgetedLLM(inner, provider="ollama", model="qwen2.5:7b")


def test_the_recordings_are_keyed_by_the_current_prompt_tag():
    """A prompt edit fails this until the responses are re-recorded (no drift)."""
    recorded = _recordings()
    assert recorded["prompt_tag"] == current_prompt_tag()
    assert recorded["provenance"] == "authored — no local Ollama on the build machine"
    labels = {i["metadata"]["label"] for t in ("classification", "root_cause") for i in _golden(t)}
    assert set(recorded["items"]) == labels


_UNAVAILABLE = (
    "Flakiness lookup unavailable: no authorized investigation context.",
    "Stack trace lookup unavailable: no authorized investigation context.",
    "Memory recall unavailable: no project context bound to this investigation (recall is project-scoped).",
)


@pytest.mark.asyncio
async def test_llm_tier_replays_through_the_budgeted_wrapper(gates):
    """The recorded golden replay, end to end through ``create_react_agent``.

    Also the contract's open point 5: a replay hands the ReAct tools NO
    project -- every tool the model calls answers "unavailable" and none
    reaches Postgres, Mongo, recall or the integrations -- while the spend is
    charged to the run's project.
    """
    recorded = _recordings()["items"]
    results: dict[str, runner.ItemOutcome] = {}
    for task_type in ("classification", "root_cause"):
        for index, item in enumerate(_golden(task_type)):
            label = item["metadata"]["label"]
            _scripted(gates, recorded[label])
            results[label] = await _replay(task_type, item, index, tier="llm")

    statuses = {label: (o.status, o.error_code) for label, o in results.items()}
    assert statuses.pop("misclassified_flaky_as_bug") == ("incorrect", None)  # the ReAct answer was wrong
    # A small model answered prose where the prompt asks for JSON: the triage
    # returned its canned stub -- an error, never scored (fail closed).
    assert statuses.pop("insufficient_evidence") == ("error", "tier_fallback")
    assert set(statuses.values()) == {("correct", None)}
    assert results["insufficient_evidence"].error_text == (
        "The llm engine was unavailable; the canned fallback answered instead, so the item is not scored."
    )
    assert results["race_condition_flaky"].output["failure_category"] == "FLAKY"
    assert results["clear_npe_root_cause"].score["mentions_line"] is True
    assert {o.mode_used for label, o in results.items() if label != "insufficient_evidence"} == {"llm"}

    # Every model call went through the wrapper's gates, charged to the project.
    calls_made = sum(len(recorded[label]) for label in results)
    assert gates["reserve"] == calls_made
    assert set(gates["scopes"]) == {PROJECT}

    # Open point 5: the ReAct tools got no project and returned no project data.
    assert gates["touched"] == []
    observations = [
        str(step[1])
        for call in gates["audit"].await_args_list
        for step in (call.args[3] or [])
    ]
    assert len(observations) == 5  # the recordings' 3 + 1 + 1 tool calls; the fast path calls none
    assert all(any(text in obs for text in _UNAVAILABLE) for obs in observations), observations
    test_case_ids = {call.args[0] for call in gates["audit"].await_args_list}
    assert all(tid.startswith("ai-eval:fpA1b2C3d4:") for tid in test_case_ids)


@pytest.mark.asyncio
async def test_without_a_project_every_react_tool_answers_unavailable(gates):
    """Open point 5 without the ReAct loop (runs where ``create_react_agent`` is
    absent too): the replayed test case carries no project / run id (pinned
    above), so the triage binds no investigation context and recall gets no
    project -- and under exactly that state each of the six REAL tool copies
    the triage hands the model answers "unavailable", touching no store."""
    from app.tools.investigation_context import (
        InvestigationContext,
        get_investigation_context,
        reset_investigation_context,
        set_investigation_context,
    )
    from app.tools.recall_memory import reset_recall_context, set_recall_context

    case = runner.replay_test_case("fpA1b2C3d4", 0, _golden("classification")[0]["input"], None)
    # What run_triage_agent binds from the case: a context only for a project AND a run.
    project, run = case.get("project_id"), case.get("run_id")
    bound = set_investigation_context(InvestigationContext(
        project_id=str(project), run_id=str(run), test_case_id=case["test_case_id"], test_name=case["test_name"],
        service_name="payments", timestamp="2026-10-25T09:00:00Z", ocp_pod_name="runner-a1", ocp_namespace="qa",
    )) if project and run else None
    token = set_recall_context(project_id=project, test_name=case["test_name"], error_message=case["error_message"])
    try:
        assert get_investigation_context() is None
        tools = {tool.name: tool for tool in triage._get_tools()}
        inputs = {
            "fetch_allure_stacktrace": case["test_case_id"],
            "fetch_rest_api_payload": case["test_case_id"],
            "query_splunk_logs": '{"service_name": "payments", "timestamp_utc": "2026-10-25T09:00:00Z"}',
            "check_test_flakiness": case["test_name"],
            "analyze_openshift_pod_events":
                '{"pod_name": "runner-a1", "namespace": "qa", "timestamp_utc": "2026-10-25T09:00:00Z"}',
            "recall_similar_failures": "NullPointerException in PaymentService",
        }
        assert set(tools) == set(inputs)
        answers = {name: await tools[name].ainvoke({"tool_input": text}) for name, text in inputs.items()}
    finally:
        reset_recall_context(token)
        if bound is not None:
            reset_investigation_context(bound)

    expected = {
        "fetch_allure_stacktrace": "Stack trace lookup unavailable: no authorized investigation context.",
        "fetch_rest_api_payload": "REST payload lookup unavailable: no authorized investigation context.",
        "query_splunk_logs": "Splunk lookup unavailable: authorized service/time context is missing.",
        "check_test_flakiness": "Flakiness lookup unavailable: no authorized investigation context.",
        "analyze_openshift_pod_events": "OpenShift lookup unavailable: authorized pod context is missing.",
        "recall_similar_failures": _UNAVAILABLE[2],
    }
    for name, sentence in expected.items():
        assert sentence in answers[name], (name, answers[name])
    assert gates["touched"] == []


class _NeverAnswers(FakeListChatModel):
    async def ainvoke(self, *args, **kwargs):
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_a_timeout_is_an_error_never_correct(gates):
    _scripted(gates, [], model=_NeverAnswers(responses=["unused"]))
    outcome = await _replay("classification", _golden("classification")[0], 0, tier="llm", timeout_s=0.2)

    assert outcome.status == "error"
    assert outcome.error_code == "item_timeout"
    assert outcome.error_text == "The engine did not answer within 0 s."
    assert outcome.output is None


@pytest.mark.asyncio
async def test_a_budget_of_one_exhausts_the_second_llm_item(gates):
    recorded = _recordings()["items"]
    items = _golden("classification")
    budget = StepLLMBudget(limit=1)
    _scripted(gates, recorded[items[0]["metadata"]["label"]])
    first = await _replay("classification", items[0], 0, tier="llm", budget=budget)
    _scripted(gates, recorded[items[1]["metadata"]["label"]])
    second = await _replay("classification", items[1], 1, tier="llm", budget=budget)

    assert first.status == "correct"
    assert (second.status, second.error_code) == ("error", "budget_exhausted")
    assert gates["reserve"] == 1  # the exhausted item called nothing
    assert budget.reasons == ["ai_eval_replay:0"]


@pytest.mark.asyncio
async def test_the_release_item_runs_the_project_policy_without_counting_a_decision():
    from app.core.metrics import release_decisions_total

    policy = ReleaseGatePolicy(version=4, rules={"rules": [{
        "id": "r1", "name": "Flaky ceiling", "type": "flaky_recurrence", "enabled": True,
        "params": {"max_flaky_tests": 3, "action": "BLOCK"},
    }]})
    context = runner.ReleasePolicyContext(policy=policy, level="project", version=4)
    item = next(i for i in _golden("release_decision") if i["metadata"]["label"] == "near_perfect_run")
    flaky = dict(item, input={**item["input"], "flaky_count": 9})

    def total() -> float:
        return sum(s.value for m in release_decisions_total.collect() for s in m.samples if s.name.endswith("_total"))

    before = total()
    blocked = await _replay("release_decision", flaky, 0, policy_context=context)
    clean = await _replay("release_decision", item, 1, policy_context=context)

    assert blocked.output["recommendation"] == "NO_GO"  # the BLOCK rule escalated a GO composite
    assert blocked.output["overall_result"] == "BLOCK"
    assert blocked.output["rule_results"] == [{"type": "flaky_recurrence", "passed": False, "action": "BLOCK"}]
    assert (blocked.output["policy_level"], blocked.output["policy_version"]) == ("project", 4)
    assert blocked.status == "incorrect" and clean.status == "correct"
    assert total() == before  # a replay is a simulation: no live decision is counted


@pytest.mark.asyncio
async def test_the_duplicate_item_never_touches_chroma(monkeypatch):
    def no_chroma(*_a, **_k):
        raise AssertionError("the replay reached ChromaDB")

    # A stand-in module, not a patch of the real one: importing app.db.chroma needs the chromadb package, which a
    # machine without it lacks; the dedup path imports it lazily, so any import of it lands here.
    chroma = types.ModuleType("app.db.chroma")
    chroma.get_chroma_client = no_chroma
    monkeypatch.setitem(sys.modules, "app.db.chroma", chroma)
    outcomes = [await _replay("duplicate_detection", item, i) for i, item in enumerate(_golden("duplicate_detection"))]

    assert all(o.output["detector_path"] == "title_ratio" for o in outcomes)
    assert [o.status for o in outcomes] == ["incorrect", "incorrect", "incorrect", "correct", "correct", "incorrect"]
    assert outcomes[5].output["predicted_duplicate"] is True and outcomes[5].output["ratio"] > 0.7


@pytest.mark.asyncio
async def test_an_engine_exception_is_an_error_and_missing_input_is_reported(monkeypatch):
    def boom(*_a, **_k):
        raise ValueError("ratio failed")

    monkeypatch.setattr("app.services.defect_promotion_service.title_duplicate_ratio", boom)
    errored = await _replay("duplicate_detection", _golden("duplicate_detection")[0], 0)
    assert (errored.status, errored.error_code) == ("error", "engine_error")
    assert errored.error_text == "The engine raised an error (ValueError)."

    missing = await _replay("classification", {"input": {"failure_category": "FLAKY"}, "expected_output": {}}, 1)
    assert (missing.status, missing.error_code) == ("error", "input_missing")
    no_rate = await _replay("release_decision", {"input": {"risk_score": 12}, "expected_output": {}}, 2)
    assert no_rate.error_code == "input_missing"


def test_an_inner_triage_fallback_is_never_the_llm_answering():
    llm = {"_routing": {"mode_used": "llm"}}
    assert runner._engine_fallback({**llm, "failure_category": "FLAKY"}, "llm") is None
    assert runner._engine_fallback({**llm, "analysis_engine": "rules"}, "llm") == "rules"
    assert runner._engine_fallback({**llm, "schema_validated": False}, "llm") == "the canned fallback"
    stub = {**llm, "root_cause_summary": "AI analysis could not complete. Reason: boom. Manual investigation required."}
    assert runner._engine_fallback(stub, "llm") == "the canned fallback"
    assert runner._engine_fallback({"_routing": {"mode_used": "rules"}}, "ml") == "rules"
    assert runner._engine_fallback({"_routing": {"mode_used": None}}, "llm") == "the canned fallback"


# ── the candidate: assertions, the offline ceiling ──────────────────────────


@pytest.fixture
def ai_config(monkeypatch):
    config = {"provider": "ollama", "model": "qwen2.5:7b", "offline_mode": True, "timeout_seconds": 300}
    seen: list = []

    async def effective(*, db=None, fresh=False):
        seen.append(db)
        return dict(config)

    monkeypatch.setattr("app.services.ai_config_resolver.get_effective_ai_config", effective)
    return config, seen


async def _resolve(task_type="classification", **candidate):
    return await runner.resolve_candidate(
        "request-session", task_type=task_type, candidate=EvalCandidateIn(**candidate), project_id=PROJECT,
    )


@pytest.mark.asyncio
async def test_a_local_llm_candidate_resolves_to_the_configured_model(ai_config):
    _config, seen = ai_config
    resolved = await _resolve(tier="llm", model="qwen2.5:7b", prompt_version=current_prompt_tag())
    assert (resolved.tier, resolved.provider, resolved.model) == ("llm", "ollama", "qwen2.5:7b")
    assert resolved.prompt_version == current_prompt_tag() and resolved.offline_mode is True
    assert seen == ["request-session"]  # read on the caller's session (no second session in a request)
    rules = await _resolve(tier="rules")
    assert (rules.model, rules.provider, rules.prompt_version) == (None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch,candidate,code,message",
    [
        ({"provider": "openai", "model": "gpt-4o"}, {"tier": "llm"}, "candidate_offline_blocked",
         "Offline mode is on and openai is not a local provider: only rules, ML or a local model can be evaluated."),
        ({}, {"tier": "llm", "model": "llama3.1:70b"}, "candidate_model_not_configured",
         "Only the configured model (qwen2.5:7b) can be evaluated; configure llama3.1:70b under Settings › AI first."),
        ({}, {"tier": "llm", "prompt_version": "react_triage@v2:000000000000"}, "candidate_prompt_not_current",
         f"Only the current prompt version ({current_prompt_tag()}) can be replayed."),
        ({"model": ""}, {"tier": "llm"}, "candidate_tier_unavailable", "No language model is configured."),
        ({}, {"tier": "gpt"}, "candidate_tier_invalid", "tier must be rules, ml or llm."),
    ],
    ids=["offline-cloud", "other-model", "stale-prompt", "no-model", "bad-tier"],
)
async def test_resolve_candidate_refuses(ai_config, patch, candidate, code, message):
    ai_config[0].update(patch)
    with pytest.raises(runner.CandidateRefused) as refused:
        await _resolve(**candidate)
    assert (refused.value.code, refused.value.message) == (code, message)


@pytest.mark.asyncio
async def test_a_deterministic_task_replays_rules_only_and_ml_needs_a_model(ai_config, monkeypatch, tmp_path):
    with pytest.raises(runner.CandidateRefused) as refused:
        await _resolve("duplicate_detection", tier="llm")
    assert refused.value.code == "candidate_tier_unsupported"
    assert refused.value.message == "duplicate_detection replays the rules tier only."

    monkeypatch.setattr(settings, "ML_MODEL_DIR", str(tmp_path))
    with pytest.raises(runner.CandidateRefused) as no_model:
        await _resolve(tier="ml")
    assert (no_model.value.code, no_model.value.message) == (
        "candidate_tier_unavailable", "No ML model is trained yet; the ML tier would answer with the rules engine.",
    )


@pytest.mark.asyncio
async def test_candidate_options_say_why_a_tier_is_unavailable(ai_config, monkeypatch, tmp_path):
    ai_config[0].update({"provider": "openai", "model": "gpt-4o"})
    monkeypatch.setattr(settings, "ML_MODEL_DIR", str(tmp_path))
    options = await runner.candidate_options("request-session")

    tiers = {t.tier: t for t in options.tiers}
    assert tiers["rules"].available is True
    assert (tiers["ml"].available, tiers["ml"].reason) == (
        False, "No ML model is trained yet; the ML tier would answer with the rules engine.",
    )
    assert (tiers["llm"].available, tiers["llm"].reason) == (
        False, "Offline mode is on and the configured model is not local (openai).",
    )
    assert (tiers["llm"].provider, tiers["llm"].model) == ("openai", "gpt-4o")
    assert options.max_items == 200 and options.offline_mode is True
    assert options.prompt_version == current_prompt_tag()
    assert options.supported_tiers_by_task["release_decision"] == ["rules"]


def test_the_item_timeout_follows_the_ai_timeout():
    assert runner.item_timeout_seconds({"timeout_seconds": 300}) == 315.0
    assert runner.item_timeout_seconds({"timeout_seconds": 5}) == 30.0
    assert runner.item_timeout_seconds({"timeout_seconds": 900}) == 600.0
    assert runner.item_timeout_seconds({}) == 30.0
