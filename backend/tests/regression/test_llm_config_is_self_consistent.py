"""Enabling a hosted LLM provider takes five settings that must agree.

Turning OpenRouter on for the homelab needed all of:

    AI_OFFLINE_MODE            false        the hard egress ceiling
    LLM_PROVIDER               openrouter   what to call
    LLM_MODEL                  <a model>
    AI_LLM_PROVIDER_ALLOWLIST  …,openrouter which providers are permitted
    AI_LLM_ALLOWED_BASE_URLS   …,openrouter.ai  which origins may be dialled

Miss either of the last two and the call dies with ``LLMPolicyViolation`` —
which reads like a broken integration rather than a deliberate refusal. Those
two are defence in depth and easy to forget precisely because the first three
are the obvious ones.

A second trap, learned the expensive way: these values must live in the
**overlay manifest**, not in a live ``kubectl patch``. The deploy re-applies the
manifest on every run, so a patched value silently reverts and the integration
stops working with no error to point at. That is exactly what happened on
2026-08-16 — OpenRouter was verified working, then the next deploy turned it
back off.

The guards are the CLASS:
  * a config that names a provider must also permit it;
  * the base default must stay offline, so enabling egress is always an
    explicit per-deployment decision rather than something inherited.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
BASE_CONFIG = REPO / "k8s" / "base" / "configmap.yaml"
HOMELAB = REPO / "k8s" / "overlays" / "homelab" / "kustomization.yaml"

pytestmark = pytest.mark.skipif(
    not BASE_CONFIG.exists(), reason="k8s manifests not present in this checkout"
)


def _values(text: str) -> dict[str, str]:
    """Scrape ``KEY: "value"`` pairs, ignoring commented-out lines."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = re.match(r'\s*([A-Z_][A-Z0-9_]*):\s*"([^"]*)"\s*$', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def test_the_scraper_actually_reads_the_manifests():
    """Every assertion below is a lookup in these dicts. If the scrape returned
    nothing they would all pass vacuously."""
    base = _values(BASE_CONFIG.read_text(encoding="utf-8"))
    assert "AI_OFFLINE_MODE" in base, "did not parse AI_OFFLINE_MODE out of the base configmap"
    assert len(base) > 5, f"base configmap scrape looks wrong: {sorted(base)}"


def test_the_shipped_default_is_offline():
    """A fresh install must not reach the internet because someone else's
    overlay wanted it to. Egress is opt-in, per deployment."""
    base = _values(BASE_CONFIG.read_text(encoding="utf-8"))
    assert base.get("AI_OFFLINE_MODE") == "true", (
        "base/configmap.yaml no longer defaults to offline — every install that "
        "does not explicitly opt out would gain outbound LLM egress"
    )


@pytest.mark.skipif(not HOMELAB.exists(), reason="homelab overlay not present")
def test_a_configured_provider_is_also_permitted():
    """The LLMPolicyViolation trap. AI_LLM_PROVIDER_ALLOWLIST is enforced
    independently of LLM_PROVIDER, so naming a provider without permitting it
    fails at call time with an error that looks like a broken integration."""
    cfg = _values(HOMELAB.read_text(encoding="utf-8"))
    provider = cfg.get("LLM_PROVIDER")
    if not provider:
        pytest.skip("overlay does not pin a provider")
    allowlist = cfg.get("AI_LLM_PROVIDER_ALLOWLIST")
    if allowlist is None:
        return  # no allowlist configured means no extra restriction
    permitted = {p.strip() for p in allowlist.split(",") if p.strip()}
    assert provider in permitted, (
        f"overlay sets LLM_PROVIDER={provider!r} but AI_LLM_PROVIDER_ALLOWLIST "
        f"is {sorted(permitted)} — every call will raise LLMPolicyViolation"
    )


@pytest.mark.skipif(not HOMELAB.exists(), reason="homelab overlay not present")
def test_a_hosted_provider_has_its_origin_permitted():
    """Same trap, the base-URL half."""
    cfg = _values(HOMELAB.read_text(encoding="utf-8"))
    provider = cfg.get("LLM_PROVIDER")
    urls = cfg.get("AI_LLM_ALLOWED_BASE_URLS")
    if not provider or urls is None:
        pytest.skip("overlay does not restrict base URLs")
    # Only hosted providers dial an external origin.
    from app.services.llm_policy_service import provider_profile

    if provider_profile(provider).residency != "remote":
        return
    assert provider in urls or any(
        provider.split("router")[0] in u for u in urls.split(",")
    ), (
        f"overlay sets LLM_PROVIDER={provider!r} but AI_LLM_ALLOWED_BASE_URLS "
        f"({urls!r}) does not include its origin — calls will be refused"
    )


@pytest.mark.skipif(not HOMELAB.exists(), reason="homelab overlay not present")
def test_enabling_egress_names_a_provider_and_model():
    """`AI_OFFLINE_MODE=false` on its own just opens the ceiling. Without a
    provider and model pinned alongside it, the deployment inherits whatever the
    base sets — which is the local one it cannot run."""
    cfg = _values(HOMELAB.read_text(encoding="utf-8"))
    if cfg.get("AI_OFFLINE_MODE") != "false":
        pytest.skip("overlay is offline; nothing to check")
    assert cfg.get("LLM_PROVIDER"), "egress enabled but no LLM_PROVIDER pinned"
    assert cfg.get("LLM_MODEL"), "egress enabled but no LLM_MODEL pinned"


@pytest.mark.skipif(not HOMELAB.exists(), reason="homelab overlay not present")
def test_the_local_provider_stays_permitted():
    """Keeping ollama in the allowlist makes reverting a one-line change to
    LLM_PROVIDER instead of another allowlist edit under pressure."""
    cfg = _values(HOMELAB.read_text(encoding="utf-8"))
    allowlist = cfg.get("AI_LLM_PROVIDER_ALLOWLIST")
    if not allowlist:
        pytest.skip("no allowlist configured")
    assert "ollama" in {p.strip() for p in allowlist.split(",")}, (
        "the local provider was removed from the allowlist — falling back to "
        "on-box inference now needs two edits, not one"
    )
