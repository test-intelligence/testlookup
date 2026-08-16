"""Every LLM provider the backend accepts must be selectable, priced and gated.

Adding OpenRouter surfaced that `anthropic` had been supported by the backend
for some time while `AIConfigPage.tsx` offered only six providers — so nobody
could select it without editing the database by hand. That is this repo's
vocabulary-subset defect: **the producer grows a value and a consumer keeps
rendering the old set.**

Three consumers have to keep step with `Settings.LLM_PROVIDER`:

  * `llm_policy_service.PROVIDER_PROFILES` — decides local vs remote. A provider
    missing here raises "Unknown LLM provider" at call time, and worse, a remote
    provider wrongly classed local would slip past the AI_OFFLINE_MODE ceiling.
  * `llm_pricing.PRICE_TABLE` — a remote provider with no price meters $0.00 per
    call, which does not read as a missing number in a report. It makes the
    per-project USD cap untrippable. That is the exact state the backend was in
    before `llm_pricing.py` existed.
  * `AIConfigPage.tsx` — the only place a human picks one.

The guard is the CLASS: enumerate the vocabulary from its single source and
assert every consumer covers it, so the next provider added cannot be
half-wired.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
CONFIG = BACKEND / "app" / "core" / "config.py"
AI_CONFIG_PAGE = REPO / "frontend" / "src" / "pages" / "settings" / "AIConfigPage.tsx"


def _declared_providers() -> set[str]:
    """The LLM_PROVIDER Literal in config.py — the single source of truth."""
    tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "LLM_PROVIDER"
        ):
            lit = node.annotation
            # Literal["a", "b", ...]
            if isinstance(lit, ast.Subscript):
                sl = lit.slice
                elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
                return {
                    e.value for e in elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                }
    raise AssertionError("LLM_PROVIDER Literal not found in config.py")


def test_the_vocabulary_parser_actually_finds_providers():
    """Everything below compares against this set. If the parse silently
    returned nothing, every test would pass vacuously."""
    providers = _declared_providers()
    assert len(providers) >= 6, f"only parsed {providers!r}"
    assert "ollama" in providers and "openrouter" in providers


# ── Policy: local vs remote, and the offline ceiling ────────────────────────


def test_every_declared_provider_has_a_policy_profile():
    """A provider with no profile raises 'Unknown LLM provider' at call time —
    discovered by a user, not by a test."""
    from app.services.llm_policy_service import PROVIDER_PROFILES

    missing = sorted(_declared_providers() - set(PROVIDER_PROFILES))
    assert not missing, f"providers with no policy profile: {missing}"


@pytest.mark.parametrize("provider", sorted(_declared_providers()))
def test_remote_providers_are_refused_when_offline(provider):
    """AI_OFFLINE_MODE is the non-bypassable egress ceiling. A new remote
    provider classed as local would quietly punch a hole through it."""
    from app.services.llm_policy_service import (
        LLMPolicyViolation,
        enforce_provider_policy,
        provider_profile,
    )

    profile = provider_profile(provider)
    if profile.residency != "remote":
        return
    with pytest.raises(LLMPolicyViolation):
        enforce_provider_policy(provider, offline=True)


def test_openrouter_is_classified_remote():
    """It fronts ~400 hosted models over the public internet. Pinned by name
    because getting this one wrong is an egress bypass, not a typo."""
    from app.services.llm_policy_service import provider_profile

    profile = provider_profile("openrouter")
    assert profile.residency == "remote"
    assert profile.supports_offline is False
    assert profile.requires_secret is True


# ── Pricing: no remote provider may meter zero ─────────────────────────────


@pytest.mark.parametrize("provider", sorted(_declared_providers()))
def test_a_remote_provider_never_reports_a_confident_zero(provider):
    """$0.00 presented as a *priced* answer is an invisible understatement that
    makes the per-project USD cap untrippable.

    Zero itself is allowed — but only when labelled ``unpriced``, which also
    emits an ``llm_call_unpriced`` warning. That distinction is the product's
    actual contract: openai/gemini/anthropic carry model-specific rows and no
    catch-all, so a brand-new model from those vendors meters zero *loudly*
    rather than silently. An earlier version of this test demanded a non-zero
    figure from every provider and failed three of them for behaving as
    designed.
    """
    from app.services.llm_pricing import SELF_HOSTED_PROVIDERS, estimate_cost

    if provider in SELF_HOSTED_PROVIDERS:
        return
    est = estimate_cost(
        provider=provider,
        model="some-model-nobody-added-to-the-table",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    if est.cost_usd == 0:
        assert est.source == "unpriced", (
            f"{provider} reports a CONFIDENT $0.00 (source={est.source}) for 2M "
            "tokens — spend is hidden and the budget cap cannot trip"
        )
    else:
        assert est.source == "priced"


def test_openrouter_never_falls_through_to_unpriced():
    """Unlike the single-vendor providers, OpenRouter proxies ~400 models and a
    deployment will routinely pick one this table has never heard of. A
    catch-all is what keeps that from metering zero."""
    from app.services.llm_pricing import estimate_cost

    est = estimate_cost(
        provider="openrouter", model="brand/new-model-2027",
        input_tokens=1_000, output_tokens=1_000,
    )
    assert est.source == "priced" and est.cost_usd > 0


def test_the_openrouter_catch_all_is_not_cheap():
    """An unrecognised OpenRouter model is more likely a frontier model than a
    budget one. Over-estimating a bill is recoverable; under-estimating is not."""
    from app.services.llm_pricing import estimate_cost

    cheap = estimate_cost(
        provider="openrouter", model="inclusionai/ling-2.6-flash",
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    unknown = estimate_cost(
        provider="openrouter", model="some/unlisted-model",
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    assert unknown.cost_usd > cheap.cost_usd * 10, (
        f"catch-all ({unknown.cost_usd}) is not conservative relative to the "
        f"cheapest listed model ({cheap.cost_usd})"
    )


# ── The UI must offer what the backend accepts ─────────────────────────────


def _ui_providers() -> set[str]:
    src = AI_CONFIG_PAGE.read_text(encoding="utf-8")
    m = re.search(r"const LLM_PROVIDERS\s*=\s*\[(.*?)\]", src, re.DOTALL)
    assert m, "LLM_PROVIDERS array not found in AIConfigPage.tsx"
    return set(re.findall(r"'([a-z]+)'", m.group(1)))


@pytest.mark.skipif(not AI_CONFIG_PAGE.exists(), reason="frontend not in this checkout")
def test_the_settings_page_offers_every_provider_the_backend_accepts():
    """The regression: `anthropic` was accepted by the backend and absent here,
    so it could only be selected by editing the database directly."""
    ui = _ui_providers()
    assert len(ui) >= 6, f"UI list parse looks wrong: {ui!r}"
    missing = sorted(_declared_providers() - ui)
    assert not missing, (
        f"backend accepts {missing} but the AI settings page does not offer them — "
        "unreachable without a manual database edit"
    )


@pytest.mark.skipif(not AI_CONFIG_PAGE.exists(), reason="frontend not in this checkout")
def test_the_settings_page_offers_nothing_the_backend_rejects():
    """The other direction: an option the backend would 422 on is worse than a
    missing one, because the user gets a failure with no explanation."""
    extra = sorted(_ui_providers() - _declared_providers())
    assert not extra, f"AI settings page offers {extra}, which the backend rejects"
