"""E4.2: the agent config resolver (architecture sections 4.1 and 4.3).

One test per precedence pair (env > ai_config > project > request), the
resolve-time offline clamp for a stored cloud provider after the environment
flips, ceilings lowered after a row was written, secrets that must not leak
into the resolved object, and the invoke route's refusal of a disabled agent.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

from app.core.config import settings
from app.services import agent_config_resolver as resolver
from app.services import agent_config_service as configs
from app.services.agent_config_service import AgentConfigPatch, OverrideRejected

SUMMARY = "agent.summary.v1"
COMMANDER = "agent.defect_commander.v1"
PROJECT_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _online_environment(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(settings, "AI_LLM_PROVIDER_ALLOWLIST", "")
    monkeypatch.setattr(resolver, "has_active_drift_pin", AsyncMock(return_value=False))


def _ai(**over):
    base = {"provider": "ollama", "model": "qwen2.5:7b", "temperature": 0.1, "max_tokens": 4096,
            "base_url": "http://localhost:11434", "offline_mode": False}
    base.update(over)
    return base


def _stored(**over):
    doc = configs.default_config(SUMMARY).model_dump(mode="json")
    for key, value in over.items():
        doc[key] = value
    return doc


def _cloud_llm(**model_over):
    model = {"tier": "llm", "llm": {"provider": "openai", "model": "gpt-4o-mini"}}
    model.update(model_over)
    return model


# -- defaults ------------------------------------------------------------------------------


def test_no_row_resolves_to_defaults_with_the_global_model_on_both_tiers():
    out = resolver.resolve(SUMMARY, global_ai_config=_ai())
    assert (out.source, out.config_version, out.patched, out.clamps) == ("default", 0, False, [])
    for tier in ("slm", "llm"):
        endpoint = out.endpoints[tier]
        assert (endpoint.provider, endpoint.model, endpoint.source) == ("ollama", "qwen2.5:7b", "ai_config")
        assert endpoint.base_url == "http://localhost:11434"


def test_endpoint_authority_fingerprint_binds_url_without_exposing_it():
    first = resolver.resolve(SUMMARY, global_ai_config=_ai(base_url="http://model-a:11434"))
    second = resolver.resolve(SUMMARY, global_ai_config=_ai(base_url="http://model-b:11434"))

    fingerprint = resolver.endpoint_authority_fingerprint(first)
    assert fingerprint != resolver.endpoint_authority_fingerprint(second)
    assert "model-a" not in fingerprint


@pytest.mark.asyncio
async def test_project_resolution_uses_the_supplied_global_snapshot(monkeypatch):
    monkeypatch.setattr(configs, "get_config_row", AsyncMock(return_value=None))
    live_read = AsyncMock(side_effect=AssertionError("must not re-read global config"))
    monkeypatch.setattr(resolver, "get_effective_ai_config", live_read)
    monkeypatch.setattr(resolver, "_apply_endpoint_residency", AsyncMock())

    resolved = await resolver.resolve_for_project(
        object(),
        PROJECT_ID,
        SUMMARY,
        global_ai_config=_ai(model="frozen-model"),
    )

    assert resolved.endpoints["slm"].model == "frozen-model"
    live_read.assert_not_awaited()


# -- env > ai_config ---------------------------------------------------------------------------


def test_env_offline_beats_a_global_config_that_turned_offline_off(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    out = resolver.resolve(SUMMARY, global_ai_config=_ai(provider="openai", model="gpt", offline_mode=False))
    assert (out.offline_mode, out.offline_mode_source, out.offline_mode_env_pinned) == (True, "env", True)
    assert out.endpoints == {"slm": None, "llm": None}
    assert {(c.field, c.layer, c.requested, c.effective) for c in out.clamps} == {
        ("model.slm.provider", "env", "openai", None),
        ("model.llm.provider", "env", "openai", None),
    }


# -- env > project -------------------------------------------------------------------------------


def test_a_stored_cloud_provider_is_clamped_after_the_env_flips_offline(monkeypatch):
    stored = _stored(model=_cloud_llm())
    assert resolver.resolve(SUMMARY, global_ai_config=_ai(), stored=stored).endpoints["llm"].provider == "openai"

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    out = resolver.resolve(SUMMARY, global_ai_config=_ai(), stored=stored, config_version=4)
    llm = out.endpoints["llm"]
    assert (llm.provider, llm.model, llm.source) == ("ollama", "qwen2.5:7b", "ai_config")
    (clamp,) = out.clamps
    assert (clamp.field, clamp.layer, clamp.requested, clamp.effective) == ("model.llm.provider", "env", "openai", "ollama")
    assert out.offline_mode_env_pinned and out.config_version == 4
    assert out.config.model.llm.provider == "openai", "the stored document is reported, the endpoint is what is clamped"


def test_the_provider_allowlist_clamps_a_stored_provider(monkeypatch):
    monkeypatch.setattr(settings, "AI_LLM_PROVIDER_ALLOWLIST", "ollama")
    out = resolver.resolve(SUMMARY, global_ai_config=_ai(), stored=_stored(model=_cloud_llm()))
    (clamp,) = out.clamps
    assert (clamp.layer, clamp.effective) == ("env", "ollama") and "AI_LLM_PROVIDER_ALLOWLIST" in clamp.reason


def test_ceilings_lowered_after_the_write_clamp_the_stored_row(monkeypatch):
    stored = _stored(timeout_seconds=100)
    stored["retry"]["max_attempts"] = 8
    monkeypatch.setattr(settings, "AGENT_MAX_ATTEMPTS_CEILING", 3)
    monkeypatch.setattr(settings, "AGENT_MAX_TIMEOUT_CEILING", 50)
    out = resolver.resolve(SUMMARY, global_ai_config=_ai(), stored=stored)
    assert (out.config.retry.max_attempts, out.config.timeout_seconds) == (3, 50)
    assert {(c.field, c.requested, c.effective) for c in out.clamps} == {
        ("timeout_seconds", 100, 50), ("retry.max_attempts", 8, 3),
    }

    monkeypatch.setattr(settings, "AI_PIPELINE_DEADLINE_SECONDS", 100)
    out = resolver.resolve(SUMMARY, global_ai_config=_ai(), stored=stored)
    assert out.config.retry.max_attempts == 2
    attempts = next(c for c in out.clamps if c.field == "retry.max_attempts")
    assert "AI_PIPELINE_DEADLINE_SECONDS=100" in attempts.reason


def test_a_row_no_clamp_can_repair_is_refused_not_guessed():
    with pytest.raises(resolver.AgentConfigInvalid) as exc:
        resolver.resolve(SUMMARY, global_ai_config=_ai(), stored=_stored(tools={"allowlist": ["a_removed_tool"]}))
    assert "unknown tools" in str(exc.value) and exc.value.agent_id == SUMMARY


def test_no_permitted_provider_leaves_the_tier_without_an_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "AI_LLM_PROVIDER_ALLOWLIST", "vllm")
    out = resolver.resolve(SUMMARY, global_ai_config=_ai(), stored=_stored(model=_cloud_llm()))
    assert out.endpoints == {"slm": None, "llm": None}
    assert all(c.effective is None for c in out.clamps) and len(out.clamps) == 2


# -- env > request ---------------------------------------------------------------------------------


def test_a_request_is_measured_against_the_clamped_project_value(monkeypatch):
    stored = _stored(timeout_seconds=100)
    stored["retry"]["max_attempts"] = 8
    monkeypatch.setattr(settings, "AGENT_MAX_ATTEMPTS_CEILING", 3)
    patch = AgentConfigPatch.model_validate({"retry": {"max_attempts": 5}})
    with pytest.raises(OverrideRejected, match="retry.max_attempts=5 loosens the project value 3"):
        resolver.resolve(SUMMARY, global_ai_config=_ai(), stored=stored, patch=patch)


# -- ai_config > project ------------------------------------------------------------------------------


def test_a_global_offline_override_clamps_a_project_cloud_provider():
    out = resolver.resolve(SUMMARY, global_ai_config=_ai(offline_mode=True), stored=_stored(model=_cloud_llm()))
    assert (out.offline_mode, out.offline_mode_source, out.offline_mode_env_pinned) == (True, "override", False)
    (clamp,) = out.clamps
    assert (clamp.layer, clamp.requested, clamp.effective) == ("ai_config", "openai", "ollama")


def test_an_allowlist_refusal_is_the_environments_even_under_a_global_offline_override(monkeypatch):
    monkeypatch.setattr(settings, "AI_LLM_PROVIDER_ALLOWLIST", "vllm")
    out = resolver.resolve(SUMMARY, global_ai_config=_ai(offline_mode=True), stored=_stored(model=_cloud_llm()))
    llm = next(c for c in out.clamps if c.field == "model.llm.provider")
    assert llm.layer == "env" and "AI_LLM_PROVIDER_ALLOWLIST" in llm.reason


# -- project > ai_config ----------------------------------------------------------------------------


def test_a_permitted_project_block_wins_over_the_global_model():
    model = {"tier": "slm", "slm": {"provider": "lmstudio", "model": "phi-3", "temperature": 0.0, "max_tokens": 512}}
    out = resolver.resolve(SUMMARY, global_ai_config=_ai(), stored=_stored(model=model))
    slm, llm = out.endpoints["slm"], out.endpoints["llm"]
    assert (slm.provider, slm.model, slm.max_tokens, slm.source) == ("lmstudio", "phi-3", 512, "project")
    assert slm.base_url is None, "the global base_url belongs to the global provider only"
    assert (llm.provider, llm.source, llm.base_url) == ("ollama", "ai_config", "http://localhost:11434")
    same = {"tier": "slm", "slm": {"provider": "ollama", "model": "qwen2.5:3b"}}
    assert resolver.resolve(SUMMARY, global_ai_config=_ai(), stored=_stored(model=same)).endpoints["slm"].base_url == "http://localhost:11434"


# -- ai_config > request -------------------------------------------------------------------------------


def test_a_request_cannot_bring_back_a_provider_the_global_config_refuses():
    with pytest.raises(ValidationError, match="Extra inputs"):
        AgentConfigPatch.model_validate({"model": {"tier": "llm", "llm": {"provider": "openai", "model": "gpt"}}})
    patch = AgentConfigPatch.model_validate({"model": {"tier": "llm"}})
    out = resolver.resolve(SUMMARY, global_ai_config=_ai(offline_mode=True), stored=_stored(model=_cloud_llm(tier="auto")), patch=patch)
    assert out.patched and out.config.model.tier == "llm"
    assert out.endpoints["llm"].provider == "ollama"


# -- project > request ----------------------------------------------------------------------------------


def test_a_request_only_tightens_the_project():
    stored = _stored(model={"tier": "slm"})
    with pytest.raises(OverrideRejected, match="looser than the project tier 'slm'"):
        resolver.resolve(SUMMARY, global_ai_config=_ai(), stored=stored, patch=AgentConfigPatch.model_validate({"model": {"tier": "llm"}}))
    out = resolver.resolve(SUMMARY, global_ai_config=_ai(), stored=stored, patch=AgentConfigPatch.model_validate({"timeout_seconds": 10}))
    assert (out.config.timeout_seconds, out.patched, out.source) == (10, True, "project")


# -- secrets ---------------------------------------------------------------------------------------------


def test_the_resolved_config_carries_no_api_keys():
    ai = _ai(openai_api_key="sk-live-secret", anthropic_api_key="ak-secret")
    out = resolver.resolve(SUMMARY, global_ai_config=ai, stored=_stored(model=_cloud_llm()))
    dumped = json.dumps(out.model_dump(mode="json"))
    assert "sk-live-secret" not in dumped and "ak-secret" not in dumped


async def test_an_invocation_snapshot_freezes_values_without_base_urls_or_keys(monkeypatch):
    patch = AgentConfigPatch.model_validate({
        "timeout_seconds": 30,
        "retry": {"max_attempts": 2},
    })
    accepted = resolver.resolve(
        SUMMARY,
        global_ai_config=_ai(api_key="sk-do-not-store"),
        config_version=7,
        patch=patch,
    )

    snapshot = resolver.freeze_for_invocation(accepted)
    encoded = json.dumps(snapshot)
    assert "base_url" not in encoded and "sk-do-not-store" not in encoded
    assert snapshot["config"]["model"]["slm"]["model"] == "qwen2.5:7b"
    assert snapshot["config"]["timeout_seconds"] == 30

    monkeypatch.setattr(
        resolver,
        "get_effective_ai_config",
        AsyncMock(return_value=_ai(model="qwen2.5:new", base_url="http://localhost:9999")),
    )
    residency = AsyncMock()
    monkeypatch.setattr(resolver, "enforce_provider_policy_async", residency)
    restored = await resolver.resolve_frozen_for_project(
        snapshot,
        expected_agent_id=SUMMARY,
        db=object(),
        project_id=PROJECT_ID,
    )

    assert restored.patched and restored.config_version == 7
    assert restored.config.retry.max_attempts == 2
    assert restored.endpoints["slm"].model == "qwen2.5:7b"
    assert restored.endpoints["slm"].base_url == "http://localhost:9999"
    assert residency.await_count == 2


async def test_a_tier_refused_at_acceptance_stays_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "AI_LLM_PROVIDER_ALLOWLIST", "vllm")
    accepted = resolver.resolve(SUMMARY, global_ai_config=_ai())
    snapshot = resolver.freeze_for_invocation(accepted)
    assert snapshot["unavailable_tiers"] == ["slm", "llm"]

    monkeypatch.setattr(settings, "AI_LLM_PROVIDER_ALLOWLIST", "")
    monkeypatch.setattr(
        resolver,
        "get_effective_ai_config",
        AsyncMock(return_value=_ai(model="now-permitted")),
    )
    monkeypatch.setattr(resolver, "enforce_provider_policy_async", AsyncMock())
    restored = await resolver.resolve_frozen_for_project(
        snapshot,
        expected_agent_id=SUMMARY,
        db=object(),
        project_id=PROJECT_ID,
    )
    assert restored.endpoints == {"slm": None, "llm": None}


async def test_a_refused_endpoint_clamp_is_sanitized_before_freezing(monkeypatch):
    accepted = resolver.resolve(SUMMARY, global_ai_config=_ai())
    refused_url = "http://secret-host.internal:11434"
    accepted.endpoints["slm"].base_url = refused_url

    async def _enforce(_provider, *, offline, base_url):
        if base_url == refused_url:
            raise resolver.LLMPolicyViolation(
                "LLM base_url host 'secret-host.internal' is not permitted"
            )

    monkeypatch.setattr(resolver, "enforce_provider_policy_async", _enforce)
    await resolver._apply_endpoint_residency(accepted)

    assert accepted.endpoints["slm"] is None
    assert accepted.endpoints["llm"] is not None
    snapshot = resolver.freeze_for_invocation(accepted)
    encoded = json.dumps(snapshot)
    assert "base_url" not in encoded
    assert "secret-host.internal" not in encoded
    assert "secret-host" not in encoded
    assert "api_key" not in encoded
    assert snapshot["clamps"][-1] == {
        "field": "model.slm.endpoint",
        "layer": "env",
        "requested": "ollama",
        "effective": None,
        "reason": "endpoint refused by live provider policy",
    }


async def test_a_pipeline_prefers_its_frozen_invocation_config(monkeypatch):
    snapshot = resolver.freeze_for_invocation(
        resolver.resolve(SUMMARY, global_ai_config=_ai())
    )
    pipeline = SimpleNamespace(
        execution_metadata={"resolved_agent_configs": {SUMMARY: snapshot}}
    )
    frozen = AsyncMock(return_value="frozen")
    live = AsyncMock(return_value="live")
    monkeypatch.setattr(resolver, "resolve_frozen_for_project", frozen)
    monkeypatch.setattr(resolver, "resolve_for_project", live)

    assert await resolver.resolve_for_pipeline("db", pipeline, PROJECT_ID, SUMMARY) == "frozen"
    frozen.assert_awaited_once_with(
        snapshot,
        expected_agent_id=SUMMARY,
        db="db",
        project_id=PROJECT_ID,
    )
    live.assert_not_awaited()


async def test_an_ordinary_pipeline_uses_its_frozen_workflow_config(monkeypatch):
    frozen_config = configs.default_config(SUMMARY).model_dump(mode="json")
    frozen_config["timeout_seconds"] = 19
    pipeline = SimpleNamespace(execution_metadata={
        "workflow_agent_configs": {SUMMARY: frozen_config},
        "agent_config_versions": {SUMMARY: 6},
        "resolved_agent_configs": {},
    })
    monkeypatch.setattr(
        resolver,
        "get_effective_ai_config",
        AsyncMock(return_value=_ai()),
    )
    live = AsyncMock(side_effect=AssertionError("live project row read"))
    monkeypatch.setattr(resolver, "resolve_for_project", live)

    resolved = await resolver.resolve_for_pipeline(
        None, pipeline, PROJECT_ID, SUMMARY
    )

    assert resolved.config.timeout_seconds == 19
    assert resolved.config_version == 6
    live.assert_not_awaited()


async def test_a_frozen_snapshot_cannot_be_replayed_for_another_agent():
    snapshot = resolver.freeze_for_invocation(
        resolver.resolve(SUMMARY, global_ai_config=_ai())
    )
    with pytest.raises(resolver.AgentConfigInvalid, match="does not match"):
        await resolver.resolve_frozen_for_project(
            snapshot,
            expected_agent_id="agent.root_cause_analysis.v1",
            db=object(),
            project_id=PROJECT_ID,
        )


async def test_frozen_restore_reapplies_a_new_eval_drift_pin(monkeypatch):
    stored = _stored(review={
        "policy": "human_required_plus_auto_reviewer",
        "auto_reviewer": True,
        "second_model_check": True,
    })
    accepted = resolver.resolve(
        SUMMARY,
        global_ai_config=_ai(),
        stored=stored,
    )
    snapshot = resolver.freeze_for_invocation(accepted)
    drift = AsyncMock(return_value=True)
    monkeypatch.setattr(resolver, "has_active_drift_pin", drift)
    monkeypatch.setattr(resolver, "get_effective_ai_config", AsyncMock(return_value=_ai()))
    monkeypatch.setattr(resolver, "enforce_provider_policy_async", AsyncMock())
    db = object()

    restored = await resolver.resolve_frozen_for_project(
        snapshot,
        expected_agent_id=SUMMARY,
        db=db,
        project_id=PROJECT_ID,
    )

    drift.assert_awaited_once_with(db, PROJECT_ID, SUMMARY)
    assert restored.config.review.policy == "human_required"
    assert restored.config.review.auto_reviewer is False
    assert restored.config.review.second_model_check is False
    assert restored.config.override_policy.allow_tier_downgrade is False
    assert {clamp.field for clamp in restored.clamps if clamp.layer == "eval_drift"} == {
        "review.policy",
        "review.auto_reviewer",
        "review.second_model_check",
        "override_policy.allow_tier_downgrade",
    }


# -- the async project resolver ---------------------------------------------------------------------------


async def test_resolve_for_project_reads_the_row_and_checks_endpoint_residency(monkeypatch):
    doc = configs.default_config(SUMMARY).model_dump(mode="json", exclude={"agent_id", "enabled", "mode"})
    row = SimpleNamespace(config=doc, enabled=False, mode="suggest", config_version=6)
    get_row = AsyncMock(return_value=row)
    monkeypatch.setattr(configs, "get_config_row", get_row)
    monkeypatch.setattr(resolver, "get_effective_ai_config", AsyncMock(return_value=_ai(base_url="http://llm.example.com:11434")))

    async def _refuse_off_box(provider, *, offline, base_url):
        raise resolver.LLMPolicyViolation(f"{base_url} is not local")

    monkeypatch.setattr(resolver, "enforce_provider_policy_async", _refuse_off_box)
    out = await resolver.resolve_for_project("db", PROJECT_ID, SUMMARY)

    get_row.assert_awaited_once_with("db", PROJECT_ID, SUMMARY)
    assert (out.source, out.config_version, out.config.enabled, out.config.mode) == ("project", 6, False, "suggest")
    assert out.endpoints == {"slm": None, "llm": None}
    assert {c.field for c in out.clamps} == {"model.slm.endpoint", "model.llm.endpoint"}


async def test_resolve_for_project_keeps_an_endpoint_that_passes_residency(monkeypatch):
    monkeypatch.setattr(configs, "get_config_row", AsyncMock(return_value=None))
    monkeypatch.setattr(resolver, "get_effective_ai_config", AsyncMock(return_value=_ai()))
    residency = AsyncMock()
    monkeypatch.setattr(resolver, "enforce_provider_policy_async", residency)
    out = await resolver.resolve_for_project("db", PROJECT_ID, SUMMARY)
    assert out.endpoints["llm"].provider == "ollama" and out.clamps == []
    assert residency.await_count == 2


# -- the invoke route --------------------------------------------------------------------------------------


def test_only_a_disabled_agent_is_refused():
    assert resolver.invocation_refusal(resolver.resolve(SUMMARY, global_ai_config=_ai()), project_id=PROJECT_ID) is None
    message = resolver.invocation_refusal(resolver.resolve(COMMANDER, global_ai_config=_ai()), project_id=PROJECT_ID)
    assert f"PUT /api/v1/projects/{PROJECT_ID}/agent-configs/{COMMANDER}" in message


def _invoke_harness(monkeypatch, resolved):
    pytest.importorskip("celery")
    from app.models.postgres import TestRun
    from app.routers import agent_invoke as router
    from app.routers.agent_invoke import AgentInvokeRequest
    from app.worker import tasks

    run = TestRun(id=uuid.uuid4(), project_id=PROJECT_ID)
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: run))
    db.add = MagicMock()
    dispatch = MagicMock()
    monkeypatch.setattr(router, "resolve_project_scope", AsyncMock())
    monkeypatch.setattr(router, "resolve_for_project", resolved)
    monkeypatch.setattr(tasks.run_agent_invocation, "apply_async", dispatch)
    body = AgentInvokeRequest(
        project_id=str(PROJECT_ID),
        input={"agent_id": SUMMARY, "payload": {"test_run_id": str(run.id)}},
    )
    return router, db, dispatch, body


async def test_invoking_a_disabled_agent_is_403_before_anything_is_recorded(monkeypatch):
    stored = _stored(enabled=False)
    resolved = AsyncMock(return_value=resolver.resolve(SUMMARY, global_ai_config=_ai(), stored=stored))
    router, db, dispatch, body = _invoke_harness(monkeypatch, resolved)
    with pytest.raises(HTTPException) as exc:
        await router.invoke_agent(agent_id=SUMMARY, body=body, response=Response(), db=db, current_user=SimpleNamespace(id=uuid.uuid4()))
    assert exc.value.status_code == 403 and "is disabled for this project" in exc.value.detail
    assert resolved.await_args.args[1:] == (PROJECT_ID, SUMMARY)
    db.add.assert_not_called()
    dispatch.assert_not_called()


def test_a_drift_pin_blocks_a_request_tier_downgrade():
    stored = _stored(model={"tier": "llm"})
    patch = AgentConfigPatch.model_validate({"model": {"tier": "slm"}})

    with pytest.raises(OverrideRejected, match="eval-drift review is closed"):
        resolver.resolve(
            SUMMARY,
            global_ai_config=_ai(),
            stored=stored,
            patch=patch,
            drift_pin_active=True,
        )


def test_a_drift_pin_requires_human_review_and_disables_auto_review():
    stored = _stored(review={
        "policy": "human_required_plus_auto_reviewer",
        "auto_reviewer": True,
        "second_model_check": True,
    })

    out = resolver.resolve(
        SUMMARY,
        global_ai_config=_ai(),
        stored=stored,
        drift_pin_active=True,
    )

    assert out.config.review.policy == "human_required"
    assert out.config.review.auto_reviewer is False
    assert out.config.review.second_model_check is False
    assert out.config.override_policy.allow_tier_downgrade is False
    assert any(clamp.layer == "eval_drift" for clamp in out.clamps)


async def test_invoking_with_a_stored_config_that_no_longer_validates_is_409(monkeypatch):
    broken = AsyncMock(side_effect=resolver.AgentConfigInvalid(SUMMARY, ["unknown tools ['gone']"]))
    router, db, dispatch, body = _invoke_harness(monkeypatch, broken)
    with pytest.raises(HTTPException) as exc:
        await router.invoke_agent(agent_id=SUMMARY, body=body, response=Response(), db=db, current_user=SimpleNamespace(id=uuid.uuid4()))
    assert exc.value.status_code == 409
    assert exc.value.detail["errors"] == ["unknown tools ['gone']"]
    assert f"/agent-configs/{SUMMARY}" in exc.value.detail["message"]
    db.add.assert_not_called()


async def test_a_loosening_invoke_override_is_reported_as_422(monkeypatch):
    rejected = AsyncMock(side_effect=OverrideRejected(["retry.max_attempts=5 loosens project value 2"]))
    router, db, dispatch, body = _invoke_harness(monkeypatch, rejected)
    body.config_overrides = AgentConfigPatch.model_validate(
        {"retry": {"max_attempts": 2}}
    )

    with pytest.raises(HTTPException) as exc:
        await router.invoke_agent(
            agent_id=SUMMARY,
            body=body,
            response=Response(),
            db=db,
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["errors"] == ["retry.max_attempts=5 loosens project value 2"]
    assert rejected.await_args.kwargs["patch"] == body.config_overrides
    db.add.assert_not_called()
    dispatch.assert_not_called()
