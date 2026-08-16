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


# ── Secret plumbing: a provider that needs a key must have a way to set it ──
#
# The AI settings page shipped API-key inputs for OpenAI and Google only, while
# the backend accepted keys for Anthropic too — and `anthropic_key_set` was
# declared on the read schema with nothing populating it, so it was permanently
# False. Selecting a provider you cannot give a key to is a dead end, and a
# "(set)" indicator that can never light up is worse than none.


# The key field is NOT uniformly "<provider>_api_key": `gemini` is configured
# with `google_api_key`, named for the vendor rather than the provider id.
# Encoded explicitly — an earlier version of these tests assumed the convention
# and reported gemini as broken when it is simply named differently.
_KEY_FIELD = {"gemini": "google_api_key"}


def _key_field(provider: str) -> str:
    return _KEY_FIELD.get(provider, f"{provider}_api_key")


def _requires_secret() -> set[str]:
    from app.services.llm_policy_service import provider_profile

    out = set()
    for provider in _declared_providers():
        try:
            if provider_profile(provider).requires_secret:
                out.add(provider)
        except Exception:  # noqa: BLE001 — absence is covered by another test
            continue
    return out


def test_the_key_field_map_matches_reality():
    """If a provider is renamed or its key field changes, the map must follow —
    otherwise every check below silently inspects a field that does not exist."""
    from app.models.schemas import AIConfigUpdate

    fields = set(AIConfigUpdate.model_fields)
    for provider in _requires_secret():
        assert _key_field(provider) in fields, (
            f"_KEY_FIELD maps {provider} to {_key_field(provider)!r}, which is not "
            "a field on AIConfigUpdate"
        )


def test_every_secret_requiring_provider_has_a_secret_field():
    """Without a SECRET_FIELDS entry the key is stored in plain app_settings
    instead of the encrypted secret_refs table — or silently dropped."""
    from app.services.secret_service import SECRET_FIELDS

    fields = SECRET_FIELDS.get("ai_config", set())
    missing = sorted(
        p for p in _requires_secret() if _key_field(p) not in fields
    )
    assert not missing, f"providers whose API key is not a registered secret: {missing}"


def test_every_secret_requiring_provider_can_be_given_a_key_via_the_api():
    """The update schema is the only way in. A provider missing here can only be
    configured by editing the database."""
    from app.models.schemas import AIConfigUpdate

    fields = set(AIConfigUpdate.model_fields)
    missing = sorted(p for p in _requires_secret() if _key_field(p) not in fields)
    assert not missing, f"AIConfigUpdate accepts no API key for: {missing}"


def test_every_secret_requiring_provider_reports_whether_its_key_is_set():
    """The read schema drives the UI's "(set)" indicator."""
    from app.models.schemas import AIConfigRead

    fields = set(AIConfigRead.model_fields)
    missing = sorted(
        p for p in _requires_secret()
        if _key_field(p).replace("_api_key", "_key_set") not in fields
    )
    assert not missing, f"AIConfigRead exposes no key_set flag for: {missing}"


def test_the_router_populates_every_key_set_flag_at_every_site():
    """`anthropic_key_set` existed on the schema with a False default and no
    code assigning it, so it read False even with a key configured — a declared
    field nothing produces, the same class as `_fallback_used`.

    Every construction site must set it, not merely one. The router builds
    AIConfigRead twice (the GET and the PUT response); an earlier version of
    this test only required the assignment to appear *somewhere*, and survived a
    mutation that removed it from one site — leaving that endpoint reporting a
    permanent False while the other was correct.
    """
    src = (BACKEND / "app" / "routers" / "app_settings.py").read_text(encoding="utf-8")
    from app.models.schemas import AIConfigRead

    sites = src.count("AIConfigRead(")
    assert sites >= 2, f"expected multiple AIConfigRead construction sites, found {sites}"

    short = []
    for field in sorted(AIConfigRead.model_fields):
        if not field.endswith("_key_set"):
            continue
        assigned = src.count(f"{field}=")
        if assigned < sites:
            short.append(f"{field} assigned at {assigned}/{sites} sites")
    assert not short, "key_set flags not populated at every response site: " + "; ".join(short)


@pytest.mark.skipif(not AI_CONFIG_PAGE.exists(), reason="frontend not in this checkout")
def test_the_settings_page_has_an_input_for_every_provider_key():
    """The user-facing half: a provider you can select but cannot give a key to
    is unusable from the UI."""
    src = AI_CONFIG_PAGE.read_text(encoding="utf-8")
    missing = sorted(
        p for p in _requires_secret() if f"'{_key_field(p)}'" not in src
    )
    assert not missing, (
        f"AI settings page has no API-key input for: {missing} — selectable but "
        "not configurable"
    )
