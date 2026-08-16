"""Regression: the readiness page must know every provider the app permits.

Bug (homelab, 2026-08-16): with ``LLM_PROVIDER=openrouter`` fully working —
verified end to end against OpenRouter's own billing counter — the AI settings
page reported::

    {"mode": "llm", "available": false, "reason": "Unknown LLM provider 'openrouter'."}

``model_status_service`` kept its own hand-maintained ``_CLOUD_PROVIDERS``
tuple. #616 taught ``llm_factory`` and ``llm_policy_service`` about OpenRouter
and this second list was never updated, so the readiness surface declared the
LLM tier dead while it was serving traffic.

This is the third instance of one class in this codebase: **a vocabulary that
one module grows and a second module renders from an older copy.** The others
were the AI settings page hardcoding six of seven providers (#617) and the
health banner treating an unknown check status as a failure (same day). The
guard is therefore the CLASS — no provider the policy service permits may be
described as unknown, and every remote provider must have a key source —
rather than a spot-check for the string "openrouter".
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.services.llm_policy_service import KNOWN_PROVIDERS, REMOTE_PROVIDERS
from app.services.model_status_service import (
    _CLOUD_KEY_SOURCES,
    _llm_chain_entry,
)

REPO = pathlib.Path(__file__).resolve().parents[3]
MSTATUS_PY = REPO / "backend" / "app" / "services" / "model_status_service.py"
AI_PAGE_TSX = REPO / "frontend" / "src" / "pages" / "settings" / "AIConfigPage.tsx"


def _entry(provider: str, *, offline: bool = False, cfg: dict | None = None):
    return _llm_chain_entry(
        provider=provider,
        llm_model="some-model",
        base_url="http://ollama:11434",
        offline_mode=offline,
        probe={"reachable": True, "models": ["some-model"]},
        cfg=cfg if cfg is not None else {},
    )


def test_the_provider_set_is_not_empty():
    """Every assertion below iterates KNOWN_PROVIDERS. An empty set would make
    them all pass vacuously."""
    assert len(KNOWN_PROVIDERS) >= 5, f"provider set looks wrong: {KNOWN_PROVIDERS}"


@pytest.mark.parametrize("provider", sorted(KNOWN_PROVIDERS))
def test_no_permitted_provider_is_reported_as_unknown(provider):
    """The regression itself, generalised: if the policy service permits it, the
    readiness page must have something honest to say about it."""
    reason = _entry(provider).get("reason") or ""
    assert "Unknown LLM provider" not in reason, (
        f"model_status_service does not recognise '{provider}', which "
        f"llm_policy_service permits — the settings page will report the LLM "
        f"tier as unavailable while it works. Reason given: {reason!r}"
    )


@pytest.mark.parametrize("provider", sorted(REMOTE_PROVIDERS))
def test_every_remote_provider_has_a_key_source(provider):
    """Without a mapping the lookup used to raise KeyError, 500ing the settings
    page — a harsher version of the same defect."""
    assert provider in _CLOUD_KEY_SOURCES, (
        f"remote provider '{provider}' has no API-key source, so its readiness "
        f"can never be determined"
    )


def test_an_unknown_provider_is_still_called_unknown():
    """The fix must not become a blanket 'everything is fine' — a genuinely
    bogus provider still has to be reported."""
    reason = _entry("not-a-real-provider").get("reason") or ""
    assert "Unknown LLM provider" in reason


# ── OpenRouter specifically, since that is what was broken live ─────────────


def test_openrouter_is_available_when_configured(monkeypatch):
    monkeypatch.setattr(
        "app.services.model_status_service.settings.OPENROUTER_API_KEY",
        "sk-or-v1-test",
        raising=False,
    )
    entry = _entry("openrouter", offline=False)
    assert entry["available"] is True, f"OpenRouter reported unavailable: {entry}"


def test_openrouter_without_a_key_is_unavailable_for_the_right_reason(monkeypatch):
    monkeypatch.setattr(
        "app.services.model_status_service.settings.OPENROUTER_API_KEY",
        "",
        raising=False,
    )
    entry = _entry("openrouter", offline=False)
    assert entry["available"] is False
    assert "No API key" in (entry["reason"] or "")


# ── The cause must be machine-readable, not re-derived by the UI ───────────
#
# The settings page branched a badge on `ollama_reachable`, so EVERY non-Ollama
# failure rendered as "Model Missing" — an unset OpenRouter key told the
# operator to install a model. `reason_code` exists so the consumer reads the
# cause the producer determined instead of guessing at it.


def test_every_unavailable_tier_carries_a_reason_code():
    """A blank code sends the UI straight back to guessing."""
    for provider in sorted(KNOWN_PROVIDERS | {"not-a-real-provider"}):
        entry = _entry(provider, offline=True)
        if entry["available"]:
            continue
        assert entry.get("reason_code"), (
            f"provider '{provider}' reports unavailable with no reason_code, so "
            f"the settings page cannot label it and falls back to guessing"
        )


def test_a_missing_key_is_not_reported_as_a_missing_model(monkeypatch):
    """The specific mislabel: distinct causes must stay distinct."""
    monkeypatch.setattr(
        "app.services.model_status_service.settings.OPENROUTER_API_KEY", "", raising=False
    )
    entry = _entry("openrouter", offline=False)
    assert entry["reason_code"] == "no_api_key", (
        f"a missing API key reports {entry.get('reason_code')!r} — the UI badge "
        f"would point the operator at the wrong problem"
    )


def test_offline_blocking_is_distinct_from_a_missing_key(monkeypatch):
    monkeypatch.setattr(
        "app.services.model_status_service.settings.OPENROUTER_API_KEY",
        "sk-or-v1-test",
        raising=False,
    )
    assert _entry("openrouter", offline=True)["reason_code"] == "offline_blocked"


@pytest.mark.skipif(not AI_PAGE_TSX.exists(), reason="frontend not in this checkout")
def test_the_ui_has_a_label_for_every_failure_code():
    """Cross-language guard for the CLASS. A new backend reason_code must gain a
    UI label rather than silently collapsing into the generic fallback."""
    emitted = set(re.findall(r'"reason_code":\s*"([a-z_]+)"', MSTATUS_PY.read_text(encoding="utf-8")))
    assert emitted, "did not parse any reason_code literals out of the service"

    page = AI_PAGE_TSX.read_text(encoding="utf-8")
    m = re.search(r"LLM_TIER_BADGES[^{]*\{([^}]*)\}", page)
    assert m, "could not find LLM_TIER_BADGES in AIConfigPage.tsx"
    labelled = set(re.findall(r"(\w+):\s*'", m.group(1)))

    # "ok" and "not_trained" are not LLM-failure states: "ok" means available
    # (no badge), "not_trained" belongs to the ML tier, which has its own badge.
    unlabelled = emitted - labelled - {"ok", "not_trained"}
    assert not unlabelled, (
        f"model_status_service can emit reason_code {sorted(unlabelled)}, which "
        f"AIConfigPage has no badge for — it would render the generic "
        f"'Unavailable' and lose the distinction the code exists to carry"
    )


def test_openrouter_is_blocked_by_offline_mode(monkeypatch):
    """AI_OFFLINE_MODE is a hard egress ceiling (#443); a remote provider must
    report blocked, not available, when it is on."""
    monkeypatch.setattr(
        "app.services.model_status_service.settings.OPENROUTER_API_KEY",
        "sk-or-v1-test",
        raising=False,
    )
    entry = _entry("openrouter", offline=True)
    assert entry["available"] is False
    assert "offline mode" in (entry["reason"] or "").lower()
