"""Centralized provider and prompt-privacy policy for LLM invocations.

The factory is the only supported construction boundary for chat models.  This
module keeps provider capability metadata and deployment policy in one place so
new agents do not each invent an offline/egress rule.  The environment remains
the hard offline ceiling; an optional provider allowlist can further restrict
both local and hosted providers without changing agent code.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from dataclasses import dataclass
from time import monotonic
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings
from app.services.agent_step_tracing import record_gen_ai_request

logger = logging.getLogger("services.llm_policy")

LOCAL_PROVIDERS = frozenset({"ollama", "lmstudio", "localai", "vllm"})
REMOTE_PROVIDERS = frozenset({"openai", "gemini", "anthropic", "openrouter"})
KNOWN_PROVIDERS = LOCAL_PROVIDERS | REMOTE_PROVIDERS


class LLMPolicyViolation(ValueError):
    """Raised before model construction or invocation when policy denies it."""


@dataclass(frozen=True)
class ProviderProfile:
    provider: str
    residency: str
    supports_offline: bool
    requires_secret: bool


PROVIDER_PROFILES: dict[str, ProviderProfile] = {
    **{
        provider: ProviderProfile(
            provider=provider,
            residency="local",
            supports_offline=True,
            requires_secret=False,
        )
        for provider in LOCAL_PROVIDERS
    },
    **{
        provider: ProviderProfile(
            provider=provider,
            residency="remote",
            supports_offline=False,
            requires_secret=True,
        )
        for provider in REMOTE_PROVIDERS
    },
}


#: Remote providers whose client CONNECTS to the configured base URL, and the
#: effective-config name of the key each one sends there. ``gemini`` and
#: ``anthropic`` are absent on purpose: ``llm_factory.get_llm`` never passes
#: them a base URL, so their key only ever goes to the provider. If one of
#: them starts honouring ``base_url``, add it here and to
#: ``_provider_own_origins``.
PROVIDER_KEY_SETTINGS: dict[str, str] = {
    "openai": "openai_api_key",
    "openrouter": "openrouter_api_key",
}

#: The name the effective AI config uses for "these keys were stored in
#: Settings > AI Configuration" (``ai_config_resolver`` fills it).
STORED_KEYS_FIELD = "stored_api_keys"


def _setting_text(name: str) -> str:
    value = getattr(settings, name, "")
    return value.strip() if isinstance(value, str) else ""


def configured_provider_allowlist() -> frozenset[str]:
    """Return the normalized optional allowlist; empty means no extra filter."""
    raw = _setting_text("AI_LLM_PROVIDER_ALLOWLIST")
    return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())


def provider_profile(provider: str) -> ProviderProfile:
    normalized = str(provider or "").strip().lower()
    try:
        return PROVIDER_PROFILES[normalized]
    except KeyError as exc:
        raise LLMPolicyViolation(f"Unknown LLM provider: '{normalized}'") from exc


#: Residency answers, with the clock that expires them.
#: ``{hostname: (answer, expires_at_monotonic)}``
_RESIDENCY_CACHE: dict[str, tuple[bool, float]] = {}

#: A confirmed-local host is stable — the check is on the invocation path and
#: re-resolving per call would put a blocking getaddrinfo in front of every
#: prompt.
_RESIDENCY_TTL_LOCAL_SECONDS = 300.0

#: A negative is NOT stable. It is returned both for "resolved to a public
#: address" and for "could not resolve at all", and the second is transient: a
#: cache that never expired meant one resolver blip disabled every LLM call on
#: that worker until it was restarted. Re-ask soon.
_RESIDENCY_TTL_DENIED_SECONDS = 30.0


def _residency_cache_clear() -> None:
    """Drop every memoised residency answer (tests, and config reloads)."""
    _RESIDENCY_CACHE.clear()


def _resolves_only_to_local_addresses(hostname: str) -> bool:
    """True when every address ``hostname`` resolves to is non-routable.

    ``AI_OFFLINE_MODE`` is documented as an egress ceiling, but residency was
    decided by the provider NAME alone: the four local provider ids are
    OpenAI-wire clients whose ``base_url`` an operator supplies, so
    ``{"llm_provider": "vllm", "base_url": "https://attacker.example/v1"}``
    shipped every prompt off-box while the policy reported a "local" provider
    (re-audit finding C3).

    Fails CLOSED. A name that does not resolve, resolves to nothing, or
    resolves to even one routable address is not local — under an offline
    ceiling "we could not prove this stays on-box" must deny, not allow.
    """
    key = hostname.strip().strip("[]")
    cached = _cached_residency(key)
    if cached is not None:
        return cached
    return _store_residency(key, _resolve_residency_uncached(key))


def _cached_residency(key: str) -> bool | None:
    cached = _RESIDENCY_CACHE.get(key)
    if cached is not None and cached[1] > monotonic():
        return cached[0]
    return None


def _store_residency(key: str, answer: bool) -> bool:
    if len(_RESIDENCY_CACHE) > 256:
        _RESIDENCY_CACHE.clear()
    ttl = _RESIDENCY_TTL_LOCAL_SECONDS if answer else _RESIDENCY_TTL_DENIED_SECONDS
    _RESIDENCY_CACHE[key] = (answer, monotonic() + ttl)
    return answer


async def host_is_local_async(hostname: str) -> bool:
    """:func:`host_is_local` with the resolution off the event loop (re-audit N11).

    ``getaddrinfo`` blocks, and a denied answer is cached for 30 s only, so
    during a resolver outage every LLM construction re-resolved ON the loop
    that also serves requests and drains streams. A cache hit returns without
    a thread hop; a miss resolves in a worker thread and fills the same cache
    the synchronous path reads.
    """
    key = hostname.strip().strip("[]")
    cached = _cached_residency(key)
    if cached is not None:
        return cached
    answer = await asyncio.to_thread(_resolve_residency_uncached, key)
    return _store_residency(key, answer)


def _resolve_residency_uncached(hostname: str) -> bool:
    """The resolution itself. Fails closed; see the caller for why."""
    candidate = hostname.strip().strip("[]")
    if not candidate:
        return False

    # A literal address needs no resolver.
    try:
        return not _is_routable(ipaddress.ip_address(candidate))
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(candidate, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return False

    addresses = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        try:
            addresses.append(ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0]))
        except ValueError:
            return False

    if not addresses:
        return False
    return all(not _is_routable(address) for address in addresses)


def host_is_local(hostname: str) -> bool:
    """Public name for the residency check, for callers outside this module.

    Notification delivery (``services/notification/egress.py``) needs the same
    question the offline LLM ceiling asks -- "does this name resolve only to
    addresses that cannot leave the box?" -- and it should get the same answer,
    from the same cache, rather than growing a second implementation that can
    drift from this one.
    """
    return _resolves_only_to_local_addresses(hostname)


#: Cloud instance-metadata services. They answer on addresses Python classifies
#: as private, so a residency test that stops at "is it private?" admits them.
#: The IPv6 AWS endpoint is not even link-local -- it sits in the ULA range --
#: so it has to be named.
_METADATA_ENDPOINTS = frozenset({
    ipaddress.ip_address("169.254.169.254"),  # AWS, GCP, Azure, OpenStack (IPv4)
    ipaddress.ip_address("fd00:ec2::254"),    # AWS IMDS over IPv6
})


#: IPv6 forms whose whole point is to carry an IPv4 address to ANOTHER network:
#: NAT64 (well-known and RFC 8215 local-use), 6to4 and Teredo. CPython marks
#: 64:ff9b:1::/48 and 2001::/23 ``is_private``, so without this a hostname
#: answering 64:ff9b:1::<public v4> passed as on-box and was translated
#: off-box by the NAT64 gateway (QA-B45-A4). The embedded IPv4's position in
#: a local-use NAT64 address depends on a prefix length the address does not
#: carry, so these ranges are refused outright: no on-box model server is
#: reached through a translator or a tunnel.
_IPV4_CARRYING_IPV6 = (
    ipaddress.ip_network("64:ff9b::/96"),    # NAT64 well-known prefix
    ipaddress.ip_network("64:ff9b:1::/48"),  # NAT64 local-use (RFC 8215)
    ipaddress.ip_network("2002::/16"),       # 6to4
    ipaddress.ip_network("2001::/32"),       # Teredo
)


def _is_routable(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether an address is NOT an acceptable on-box LLM host.

    Loopback and private-network addresses are local. Link-local addresses and
    cloud metadata endpoints are not, and are checked FIRST (re-audit N7):

    On Python 3.11, ``169.254.169.254`` reports ``is_private=True`` as well as
    ``is_link_local=True``. The old test listed link-local as a way of being
    local, but simply deleting that clause would not have helped -- the address
    was already accepted through ``is_private``. So under AI_OFFLINE_MODE an
    operator-supplied ``base_url`` of ``http://169.254.169.254/`` counted as
    "on-box" and prompts could be sent to the instance metadata service, which
    is also where cloud credentials live. No legitimate local model server
    listens on a link-local address.
    """
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if isinstance(address, ipaddress.IPv6Address) and any(
        address in network for network in _IPV4_CARRYING_IPV6
    ):
        return True
    if address.is_link_local or address in _METADATA_ENDPOINTS:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_unspecified
    )


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str | None) -> tuple[str, str, int] | None:
    """``(scheme, host, port)`` of an absolute HTTP(S) URL, else ``None``.

    Compared instead of the text so that ``https://API.OpenAI.com:443/v1`` is
    the provider and ``https://api.openai.com@other.example/`` is not.
    """
    try:
        parsed = urlparse(str(url or "").strip())
        port = parsed.port
    except ValueError:
        return None
    scheme = (parsed.scheme or "").lower()
    if scheme not in _DEFAULT_PORTS or not parsed.hostname:
        return None
    return scheme, parsed.hostname.lower().rstrip("."), port or _DEFAULT_PORTS[scheme]


def _provider_own_origins(provider: str) -> frozenset[tuple[str, str, int]]:
    """The origins that ARE the provider, for the environment-key rule.

    The provider's public endpoint, plus whatever endpoint the deployment's
    own environment names for it: that value and the environment key have the
    same owner, so sending one to the other gives nothing away.
    """
    candidates: list[str | None]
    if provider == "openai":
        candidates = [
            "https://api.openai.com",
            os.getenv("OPENAI_API_BASE"),
            os.getenv("OPENAI_BASE_URL"),
        ]
    elif provider == "openrouter":
        candidates = ["https://openrouter.ai", _setting_text("OPENROUTER_BASE_URL")]
    else:
        candidates = []
    return frozenset(origin for origin in map(_origin, candidates) if origin is not None)


def is_custom_remote_endpoint(provider: str, base_url: str | None) -> bool:
    """True when ``base_url`` would carry a remote provider's key somewhere else.

    False for local providers (they send no provider key), for remote
    providers whose client ignores the base URL, for an empty base URL, and
    for the provider's own origin.
    """
    normalized = str(provider or "").strip().lower()
    if normalized not in PROVIDER_KEY_SETTINGS or not base_url:
        return False
    return _origin(base_url) not in _provider_own_origins(normalized)


def _refuse_environment_key(provider: str, base_url: str | None) -> None:
    origin = _origin(base_url)
    shown = f"{origin[0]}://{origin[1]}:{origin[2]}" if origin else "the configured base URL"
    raise LLMPolicyViolation(
        f"The LLM base URL ({shown}) is not {provider}'s own endpoint, so the "
        f"{provider} API key from this deployment's environment is not sent to it. "
        "Store an API key for this endpoint in Settings > AI Configuration, "
        "or clear the base URL to use the provider's own endpoint."
    )


async def _key_is_stored_in_settings(provider: str) -> bool:
    """Whether Settings holds a stored key for ``provider``. Fails closed.

    Read from the same effective config ``get_llm`` takes the key from. A
    config that does not say where its keys came from (a cache entry written
    by an older build) or that cannot be read is "no stored key".
    """
    try:
        from app.services.ai_config_resolver import get_effective_ai_config

        stored = (await get_effective_ai_config()).get(STORED_KEYS_FIELD)
    except Exception:  # noqa: BLE001 -- no proof of a stored key is a refusal
        return False
    return isinstance(stored, (list, tuple)) and PROVIDER_KEY_SETTINGS[provider] in stored


def validate_base_url_for_save(
    provider: str,
    base_url: str | None,
    *,
    key_from_settings: bool,
) -> None:
    """The base-URL rules a model call applies, for ``PUT /settings/ai``.

    Raises :class:`LLMPolicyViolation` so the ADMIN is told at save time, not
    by a failed model call later. Only the rules about the URL itself: its
    form, ``AI_LLM_ALLOWED_BASE_URLS`` and the environment-key rule. A
    provider this module does not know is left to the caller.
    """
    normalized = str(provider or "").strip().lower()
    if not base_url or normalized not in PROVIDER_PROFILES:
        return
    _check_base_url(PROVIDER_PROFILES[normalized], base_url, key_from_settings=key_from_settings)


def enforce_provider_policy(
    provider: str,
    *,
    offline: bool,
    base_url: str | None = None,
    key_from_settings: bool | None = None,
) -> ProviderProfile:
    """Fail closed before constructing a provider client.

    ``key_from_settings`` answers "was this provider's key stored in Settings
    > AI Configuration?" for the environment-key rule. This synchronous form
    cannot look it up, so leaving it out means "no".

    ``AI_OFFLINE_MODE`` remains the non-bypassable egress ceiling.  Operators
    may additionally set ``AI_LLM_PROVIDER_ALLOWLIST`` to a comma-separated
    set of approved provider identifiers. A supplied base URL must be an
    absolute HTTP(S) origin and may be restricted with
    ``AI_LLM_ALLOWED_BASE_URLS``. Provider clients still own connection
    validation and secrets never enter this policy result.
    """
    profile, residency_host = _enforce_static_policy(
        provider, offline=offline, base_url=base_url, key_from_settings=bool(key_from_settings)
    )
    if residency_host is not None and not _resolves_only_to_local_addresses(residency_host):
        _refuse_off_box(residency_host)
    return profile


async def enforce_provider_policy_async(
    provider: str,
    *,
    offline: bool,
    base_url: str | None = None,
    key_from_settings: bool | None = None,
) -> ProviderProfile:
    """:func:`enforce_provider_policy` for async callers (re-audit N11).

    Identical decisions; the residency lookup runs in a worker thread so a
    slow or failing resolver cannot stall the event loop. When the caller does
    not say whether the provider key was stored in Settings, this form looks
    it up in the effective AI config (only for a custom remote endpoint).
    """
    if key_from_settings is None:
        key_from_settings = (
            not offline
            and is_custom_remote_endpoint(provider, base_url)
            and await _key_is_stored_in_settings(str(provider).strip().lower())
        )
    profile, residency_host = _enforce_static_policy(
        provider, offline=offline, base_url=base_url, key_from_settings=key_from_settings
    )
    if residency_host is not None and not await host_is_local_async(residency_host):
        _refuse_off_box(residency_host)
    return profile


def _refuse_off_box(hostname: str) -> None:
    raise LLMPolicyViolation(
        f"AI_OFFLINE_MODE=true but LLM base_url host '{hostname}' is "
        "not a loopback/private address — refusing to send prompts off-box"
    )


def _enforce_static_policy(
    provider: str,
    *,
    offline: bool,
    base_url: str | None,
    key_from_settings: bool = False,
) -> tuple[ProviderProfile, str | None]:
    """Every check that needs no name resolution.

    Returns the profile and, when the offline ceiling must still establish
    residency, the hostname to resolve (``None`` otherwise).
    """
    profile = provider_profile(provider)
    allowlist = configured_provider_allowlist()
    if allowlist and profile.provider not in allowlist:
        raise LLMPolicyViolation(
            f"LLM provider '{profile.provider}' is not present in AI_LLM_PROVIDER_ALLOWLIST"
        )
    if profile.residency == "remote" and offline:
        raise LLMPolicyViolation(
            f"AI_OFFLINE_MODE=true but LLM_PROVIDER={profile.provider} — refusing to call external API"
        )
    if base_url:
        hostname = _check_base_url(profile, base_url, key_from_settings=key_from_settings)
        # Residency is a property of the ENDPOINT, not of the provider's name.
        # A nominally-local provider pointed at a public host is egress, so the
        # offline ceiling refuses it even when an allowlist permitted the origin
        # (re-audit C3) — AI_OFFLINE_MODE is documented as non-bypassable.
        #
        # This is the early, readable refusal. The connection itself is pinned
        # to the addresses validated at connect time (llm_egress, re-audit N8),
        # so a name that re-resolves between here and the connect is refused
        # there.
        if offline:
            return profile, hostname
    return profile, None


def _check_base_url(profile: ProviderProfile, base_url: str, *, key_from_settings: bool) -> str:
    """The rules about the URL itself; returns its hostname."""
    parsed = urlparse(str(base_url).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LLMPolicyViolation("LLM base_url must be an absolute HTTP(S) origin")
    # An operator's explicit allowlist is the cheapest and most specific
    # deny, so it reports first; it needs no name resolution.
    allowed_origins = _setting_text("AI_LLM_ALLOWED_BASE_URLS")
    if allowed_origins:
        candidate = f"{parsed.scheme}://{parsed.netloc}".lower()
        allowed = {
            item.strip().rstrip("/").lower()
            for item in allowed_origins.split(",")
            if item.strip()
        }
        if candidate not in allowed:
            raise LLMPolicyViolation("LLM base_url is not present in AI_LLM_ALLOWED_BASE_URLS")
    # A provider key from the environment goes to the provider and nowhere
    # else. An ADMIN can type any base URL, and the environment key belongs to
    # whoever runs the deployment: before this rule, provider "openai" with a
    # base URL of the ADMIN's choosing posted OPENAI_API_KEY to that host as a
    # bearer token. A key the ADMIN stored in Settings for the endpoint is
    # theirs to send. Listing the origin in AI_LLM_ALLOWED_BASE_URLS does not
    # lift this: it says where prompts may go, not where the key may.
    if not key_from_settings and is_custom_remote_endpoint(profile.provider, base_url):
        _refuse_environment_key(profile.provider, base_url)
    return parsed.hostname


def sanitize_invocation(value: Any, *, stats: dict[str, int] | None = None) -> Any:
    """Recursively sanitize strings in a model invocation without raw logging."""
    from app.services.privacy_service import sanitize_for_llm

    counters = stats if stats is not None else {"redacted_strings": 0}
    if isinstance(value, str):
        safe = sanitize_for_llm(value)
        if safe != value:
            counters["redacted_strings"] = int(counters.get("redacted_strings", 0)) + 1
        return safe
    if isinstance(value, dict):
        return {key: sanitize_invocation(item, stats=counters) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_invocation(item, stats=counters) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_invocation(item, stats=counters) for item in value)

    # LangChain BaseMessage is a Pydantic model.  Copying only content keeps
    # role/tool metadata intact while ensuring message text crosses the same
    # redaction boundary as plain strings.
    try:
        from langchain_core.messages import BaseMessage

        if isinstance(value, BaseMessage):
            content = sanitize_invocation(value.content, stats=counters)
            if hasattr(value, "model_copy"):
                return value.model_copy(update={"content": content})
            return value.copy(update={"content": content})
        # ChatPromptValue and StringPromptValue are common chain boundaries;
        # copy their text/message payloads instead of allowing an opaque prompt
        # object to bypass the sanitizer.
        if hasattr(value, "messages"):
            messages = sanitize_invocation(value.messages, stats=counters)
            if hasattr(value, "model_copy"):
                return value.model_copy(update={"messages": messages})
            if hasattr(value, "copy"):
                return value.copy(update={"messages": messages})
        if hasattr(value, "text") and isinstance(value.text, str):
            text = sanitize_invocation(value.text, stats=counters)
            if hasattr(value, "model_copy"):
                return value.model_copy(update={"text": text})
            if hasattr(value, "copy"):
                return value.copy(update={"text": text})
    except Exception as exc:
        raise LLMPolicyViolation("llm_prompt_sanitization_failed") from exc
    if hasattr(value, "to_messages") or hasattr(value, "to_string"):
        raise LLMPolicyViolation("llm_prompt_sanitization_unsupported")
    return value


def record_invocation_audit(
    *,
    provider: str,
    model: str,
    stats: dict[str, int],
) -> None:
    """Record bounded, secret-free invocation metadata in the active context."""
    from app.services.pipeline_budget_service import get_pipeline_budget_context

    context = get_pipeline_budget_context()
    traced_provider = str(provider)[:40]
    traced_model = str(model)[:120]
    event = {
        "provider": traced_provider,
        "model": traced_model,
        "redacted_strings": max(int(stats.get("redacted_strings", 0)), 0),
    }
    try:
        record_gen_ai_request(provider=traced_provider, model=traced_model)
    except Exception:  # noqa: BLE001 -- tracing must never block inference
        logger.debug("llm_invocation_trace_failed", exc_info=True)
    logger.info("llm_invocation_policy", extra=event)
    if context is not None:
        events = context.get("llm_privacy_events")
        if not isinstance(events, list):
            events = []
        context["llm_privacy_events"] = [*events[-99:], event]
