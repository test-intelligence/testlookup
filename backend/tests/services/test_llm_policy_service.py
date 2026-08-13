from __future__ import annotations

import pytest


def test_provider_profiles_distinguish_local_and_remote():
    from app.services.llm_policy_service import provider_profile

    assert provider_profile("ollama").residency == "local"
    assert provider_profile("OpenAI").residency == "remote"
    assert provider_profile("ollama").supports_offline is True
    assert provider_profile("openai").requires_secret is True


def test_unknown_provider_fails_closed():
    from app.services.llm_policy_service import LLMPolicyViolation, provider_profile

    with pytest.raises(LLMPolicyViolation, match="Unknown LLM provider"):
        provider_profile("untrusted-endpoint")


def test_optional_provider_allowlist_is_enforced(monkeypatch):
    from app.core.config import settings
    from app.services.llm_policy_service import LLMPolicyViolation, enforce_provider_policy

    monkeypatch.setattr(settings, "AI_LLM_PROVIDER_ALLOWLIST", "ollama,vllm")
    assert enforce_provider_policy("ollama", offline=True).residency == "local"
    with pytest.raises(LLMPolicyViolation, match="not present"):
        enforce_provider_policy("openai", offline=False)


def test_remote_provider_still_obeys_offline_ceiling():
    from app.services.llm_policy_service import LLMPolicyViolation, enforce_provider_policy

    with pytest.raises(LLMPolicyViolation, match="AI_OFFLINE_MODE"):
        enforce_provider_policy("anthropic", offline=True)


def test_base_url_requires_http_and_optional_origin_allowlist(monkeypatch):
    from app.core.config import settings
    from app.services.llm_policy_service import LLMPolicyViolation, enforce_provider_policy

    with pytest.raises(LLMPolicyViolation, match="absolute HTTP"):
        enforce_provider_policy("ollama", offline=True, base_url="file:///tmp/model")
    monkeypatch.setattr(settings, "AI_LLM_ALLOWED_BASE_URLS", "http://localhost:11434")
    assert enforce_provider_policy(
        "ollama", offline=True, base_url="http://localhost:11434/api"
    ).provider == "ollama"
    with pytest.raises(LLMPolicyViolation, match="not present"):
        enforce_provider_policy("ollama", offline=True, base_url="http://other:11434")


def test_invocation_sanitizer_redacts_nested_values_and_messages():
    from langchain_core.messages import HumanMessage
    from langchain_core.prompt_values import ChatPromptValue

    from app.services.llm_policy_service import sanitize_invocation

    stats = {"redacted_strings": 0}
    message = HumanMessage(content="contact user@example.com token=secret-value")
    result = sanitize_invocation(
        {"prompt": [message, "password=hunter2"], "count": 1}, stats=stats
    )
    assert result["prompt"][0].content == (
        "contact [REDACTED_EMAIL] token=[REDACTED]"
    )
    assert result["prompt"][1] == "password=[REDACTED]"
    assert stats["redacted_strings"] == 2
    prompt = ChatPromptValue(messages=[HumanMessage(content="token=secret-value")])
    safe_prompt = sanitize_invocation(prompt, stats=stats)
    assert safe_prompt.messages[0].content == "token=[REDACTED]"


@pytest.mark.asyncio
async def test_budgeted_llm_redacts_at_invocation_boundary_and_audits():
    from app.services.llm_factory import BudgetedLLM
    from app.services.pipeline_budget_service import (
        append_provider_policy_audit,
        get_pipeline_budget_context,
        reset_pipeline_budget_context,
        set_pipeline_budget_context,
    )

    class FakeModel:
        def __init__(self):
            self.received = None

        async def ainvoke(self, value):
            self.received = value
            return {"ok": True}

    fake = FakeModel()
    model = BudgetedLLM(fake, provider="ollama", model="local-test")
    token = set_pipeline_budget_context(blocked=False)
    try:
        await model.ainvoke("user@example.com password=hunter2")
        assert fake.received == "[REDACTED_EMAIL] password=[REDACTED]"
        context = get_pipeline_budget_context()
        assert context["llm_privacy_events"] == [
            {"provider": "ollama", "model": "local-test", "redacted_strings": 1}
        ]
        metadata = {}
        append_provider_policy_audit(metadata, context["llm_privacy_events"])
        assert metadata["provider_policy_audit"] == [
            {"provider": "ollama", "model": "local-test", "redacted_strings": 1}
        ]
    finally:
        reset_pipeline_budget_context(token)
