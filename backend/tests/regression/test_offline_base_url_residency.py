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

import socket

import pytest

from app.services import llm_policy_service
from app.services.llm_policy_service import (
    LLMPolicyViolation,
    _residency_cache_clear,
    _resolves_only_to_local_addresses,
    enforce_provider_policy,
)


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    """The resolver memoizes; a stale entry would leak between cases."""
    _residency_cache_clear()
    yield
    _residency_cache_clear()


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
    _residency_cache_clear()

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
    _residency_cache_clear()

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
    _residency_cache_clear()

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
    _residency_cache_clear()

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
    _residency_cache_clear()

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
    _residency_cache_clear()

    with pytest.raises(LLMPolicyViolation) as exc:
        enforce_provider_policy("ollama", offline=True, base_url="http://other:11434")
    assert "not present in AI_LLM_ALLOWED_BASE_URLS" in str(exc.value)


# ── The cache must forget a failure ──────────────────────────────────────
#
# The first cut memoised this with @lru_cache, which never expires. A negative
# is returned both for "resolved to a public address" and for "could not
# resolve at all" — and the second is transient. One resolver blip therefore
# pinned "not local" for the life of the worker process, and every later LLM
# call was refused until someone restarted it. Under AI_OFFLINE_MODE that reads
# as the offline ceiling working, which is why it would not have been reported
# as a bug.


def test_a_failed_resolution_is_retried_once_its_ttl_expires(monkeypatch):
    calls = []

    def _flaky(host, *args, **kwargs):
        calls.append(host)
        if len(calls) == 1:
            raise socket.gaierror("temporary failure in name resolution")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(llm_policy_service.socket, "getaddrinfo", _flaky)
    _residency_cache_clear()

    assert _resolves_only_to_local_addresses("blip.internal") is False
    # Still cached — the TTL exists to keep getaddrinfo off the hot path.
    assert _resolves_only_to_local_addresses("blip.internal") is False
    assert len(calls) == 1

    # Advance by a fixed, realistic interval rather than by the constant
    # itself: reading the TTL here would let ANY value pass, including the
    # never-expiring one this test exists to rule out.
    assert llm_policy_service._RESIDENCY_TTL_DENIED_SECONDS <= 120, (
        "a DNS failure stays cached for more than two minutes, which is long "
        "enough for a blip to look like a working offline ceiling"
    )
    base = llm_policy_service.monotonic()
    monkeypatch.setattr(llm_policy_service, "monotonic", lambda: base + 120.0)

    assert _resolves_only_to_local_addresses("blip.internal") is True, (
        "a transient DNS failure is still pinned after its TTL — one blip "
        "disables every LLM call on this worker until it restarts"
    )


def test_a_negative_expires_sooner_than_a_positive():
    """The asymmetry is the point, so state it as a fact and not a comment."""
    assert (
        llm_policy_service._RESIDENCY_TTL_DENIED_SECONDS
        < llm_policy_service._RESIDENCY_TTL_LOCAL_SECONDS
    )


def test_a_confirmed_local_host_is_not_re_resolved(monkeypatch):
    """The cache still has to do its job — this runs before every invocation."""
    calls = []

    def _counting(host, *args, **kwargs):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr(llm_policy_service.socket, "getaddrinfo", _counting)
    _residency_cache_clear()

    for _ in range(5):
        assert _resolves_only_to_local_addresses("ollama.svc.cluster.local") is True
    assert len(calls) == 1


def test_the_cache_cannot_grow_without_bound(monkeypatch):
    """A caller-supplied hostname must not be an unbounded memory sink."""
    monkeypatch.setattr(
        llm_policy_service.socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))],
    )
    _residency_cache_clear()

    for i in range(400):
        _resolves_only_to_local_addresses("host-%d.internal" % i)

    assert len(llm_policy_service._RESIDENCY_CACHE) <= 257


# ── The two provider lists must not drift ────────────────────────────────
#
# AIConfigUpdate.llm_provider constrains the value with a literal regex, while
# the policy that decides what a provider MEANS keeps its own set. Nothing
# links them, so adding a provider in one place and not the other either
# rejects a supported provider at the API boundary or admits one the policy has
# never heard of. Derive the comparison from both sources rather than typing a
# third list here.


def test_the_api_regex_admits_exactly_the_providers_the_policy_knows():
    import re

    from app.models.schemas import AIConfigUpdate
    from app.services.llm_policy_service import KNOWN_PROVIDERS

    pattern = AIConfigUpdate.model_fields["llm_provider"].metadata[0].pattern
    compiled = re.compile(pattern)

    accepted = {p for p in KNOWN_PROVIDERS if compiled.fullmatch(p)}
    assert accepted == set(KNOWN_PROVIDERS), (
        "the API rejects providers the policy supports: "
        + str(sorted(set(KNOWN_PROVIDERS) - accepted))
    )

    # And nothing outside the policy's vocabulary gets through.
    inner = pattern.strip("^$()")
    assert set(inner.split("|")) == set(KNOWN_PROVIDERS), (
        "the API accepts a provider the policy has no profile for, so its "
        "residency and allowlist rules would never run on it: "
        + str(sorted(set(inner.split("|")) - set(KNOWN_PROVIDERS)))
    )
