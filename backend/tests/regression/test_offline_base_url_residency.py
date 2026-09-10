"""AI_OFFLINE_MODE must gate the endpoint, not the provider's name.

Re-audit finding C3. ``enforce_provider_policy`` decided residency purely from
the provider identifier: ``ollama``/``lmstudio``/``localai``/``vllm`` were
hard-coded ``residency="local"`` and skipped the offline check entirely. All
four are OpenAI-wire clients whose ``base_url`` an operator supplies through
``PUT /api/v1/settings/ai``, and ``AI_LLM_ALLOWED_BASE_URLS`` is empty by
default (treated as "no restriction").

So on a deployment whose entire premise is ``AI_OFFLINE_MODE=true``:

    {"llm_provider": "vllm", "base_url": "https://attacker.example/v1"}

was accepted, and every subsequent ``get_llm()`` call shipped its prompt
off-box. No API key was even required — the vllm branch passes a literal.

The fix resolves the base_url host and refuses anything routable while
offline, and fails CLOSED when the host cannot be resolved at all.
"""
from __future__ import annotations

import pytest

from app.services.llm_policy_service import (
    LLMPolicyViolation,
    _resolves_only_to_local_addresses,
    enforce_provider_policy,
)


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    """The resolver memoizes; a stale entry would leak between cases."""
    _resolves_only_to_local_addresses.cache_clear()
    yield
    _resolves_only_to_local_addresses.cache_clear()


# ── The exact bypass the audit reported ──────────────────────────────────────


@pytest.mark.parametrize("provider", ["vllm", "lmstudio", "localai", "ollama"])
def test_offline_refuses_a_public_base_url_for_every_local_provider(provider):
    """The reported payload, across all four nominally-local providers."""
    with pytest.raises(LLMPolicyViolation) as exc:
        enforce_provider_policy(
            provider, offline=True, base_url="https://attacker.example/v1"
        )
    assert "not a loopback/private address" in str(exc.value)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://8.8.8.8:11434",
        "https://api.openai.com/v1",
        "http://[2606:4700:4700::1111]:8080/v1",
    ],
)
def test_offline_refuses_routable_hosts_by_literal_address(base_url):
    """A literal public address needs no DNS to be refused."""
    with pytest.raises(LLMPolicyViolation):
        enforce_provider_policy("vllm", offline=True, base_url=base_url)


# ── What must keep working ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://10.4.2.9:8000/v1",
        "http://192.168.1.50:1234/v1",
        "http://172.16.3.4:8080",
        "http://[::1]:11434",
    ],
)
def test_offline_allows_loopback_and_private_endpoints(base_url):
    """An air-gapped deployment's real endpoints must still construct."""
    profile = enforce_provider_policy("ollama", offline=True, base_url=base_url)
    assert profile.provider == "ollama"


def test_online_deployments_are_unaffected():
    """The residency check is scoped to the offline ceiling only."""
    profile = enforce_provider_policy(
        "vllm", offline=False, base_url="https://vendor.example/v1"
    )
    assert profile.provider == "vllm"


def test_no_base_url_still_uses_the_provider_default():
    """Omitting base_url must not start requiring resolution."""
    assert enforce_provider_policy("ollama", offline=True).provider == "ollama"


def test_remote_providers_are_still_refused_offline():
    """The pre-existing residency rule must survive the new check."""
    with pytest.raises(LLMPolicyViolation) as exc:
        enforce_provider_policy("openai", offline=True)
    assert "refusing to call external API" in str(exc.value)


# ── Fail-closed behaviour ────────────────────────────────────────────────────


def test_unresolvable_host_is_refused_not_allowed(monkeypatch):
    """"Cannot prove it is local" must deny under an egress ceiling."""
    import socket as socket_module

    def _boom(*_args, **_kwargs):
        raise socket_module.gaierror("no such host")

    monkeypatch.setattr(
        "app.services.llm_policy_service.socket.getaddrinfo", _boom
    )
    _resolves_only_to_local_addresses.cache_clear()

    with pytest.raises(LLMPolicyViolation):
        enforce_provider_policy(
            "vllm", offline=True, base_url="http://does-not-resolve.invalid/v1"
        )


def test_split_horizon_host_with_one_public_address_is_refused(monkeypatch):
    """A name resolving to both a private and a public address is egress."""
    import socket as socket_module

    def _mixed(*_args, **_kwargs):
        return [
            (socket_module.AF_INET, None, None, "", ("10.0.0.5", 443)),
            (socket_module.AF_INET, None, None, "", ("93.184.216.34", 443)),
        ]

    monkeypatch.setattr(
        "app.services.llm_policy_service.socket.getaddrinfo", _mixed
    )
    _resolves_only_to_local_addresses.cache_clear()

    with pytest.raises(LLMPolicyViolation):
        enforce_provider_policy(
            "vllm", offline=True, base_url="http://split-horizon.example/v1"
        )


def test_ipv4_mapped_ipv6_public_address_is_refused(monkeypatch):
    """::ffff:93.184.216.34 must not read as a non-routable IPv6 host."""
    import socket as socket_module

    def _mapped(*_args, **_kwargs):
        return [
            (socket_module.AF_INET6, None, None, "", ("::ffff:93.184.216.34", 443, 0, 0))
        ]

    monkeypatch.setattr(
        "app.services.llm_policy_service.socket.getaddrinfo", _mapped
    )
    _resolves_only_to_local_addresses.cache_clear()

    with pytest.raises(LLMPolicyViolation):
        enforce_provider_policy(
            "vllm", offline=True, base_url="http://mapped.example/v1"
        )


def test_empty_resolution_is_refused(monkeypatch):
    """Zero addresses proves nothing, so it must not pass."""
    monkeypatch.setattr(
        "app.services.llm_policy_service.socket.getaddrinfo",
        lambda *a, **k: [],
    )
    _resolves_only_to_local_addresses.cache_clear()

    with pytest.raises(LLMPolicyViolation):
        enforce_provider_policy(
            "vllm", offline=True, base_url="http://empty.example/v1"
        )


# ── The write path that reached the policy ───────────────────────────────────


def test_settings_payload_rejects_an_unknown_provider_id():
    """AIConfigUpdate accepted any string, including ones with no profile."""
    from pydantic import ValidationError

    from app.models.schemas import AIConfigUpdate

    with pytest.raises(ValidationError):
        AIConfigUpdate(llm_provider="attacker-proxy")


@pytest.mark.parametrize(
    "provider",
    ["ollama", "lmstudio", "localai", "vllm", "openai", "gemini", "anthropic", "openrouter"],
)
def test_settings_payload_still_accepts_every_supported_provider(provider):
    from app.models.schemas import AIConfigUpdate

    assert AIConfigUpdate(llm_provider=provider).llm_provider == provider


# ── The allowlist must not become a bypass ───────────────────────────────────


def test_an_allowlisted_public_origin_is_still_refused_offline(monkeypatch):
    """AI_OFFLINE_MODE outranks AI_LLM_ALLOWED_BASE_URLS.

    The allowlist narrows what an online deployment may call. It is not a
    permit to leave the box: an operator who allowlists a public origin and
    also sets the offline ceiling gets the ceiling, because that flag is
    documented as non-bypassable. The two checks are ordered allowlist-first
    only so the cheaper, more specific denial reports first — never so that
    passing the allowlist can skip the residency check.
    """
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "AI_LLM_ALLOWED_BASE_URLS", "https://attacker.example"
    )
    _resolves_only_to_local_addresses.cache_clear()

    with pytest.raises(LLMPolicyViolation) as exc:
        enforce_provider_policy(
            "vllm", offline=True, base_url="https://attacker.example/v1"
        )
    assert "not a loopback/private address" in str(exc.value)


def test_allowlist_denial_reports_before_resolution(monkeypatch):
    """A non-allowlisted origin reports the allowlist reason, not a DNS one."""
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "AI_LLM_ALLOWED_BASE_URLS", "http://localhost:11434"
    )
    _resolves_only_to_local_addresses.cache_clear()

    with pytest.raises(LLMPolicyViolation) as exc:
        enforce_provider_policy("ollama", offline=True, base_url="http://other:11434")
    assert "not present in AI_LLM_ALLOWED_BASE_URLS" in str(exc.value)
