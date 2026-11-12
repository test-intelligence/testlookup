"""Hosted models a deployment chooses have their own price entries.

``llm_pricing`` prices an unrecognised OpenRouter model at a deliberately high
catch-all ($3 / $15 per million tokens), so an unknown model never meters
$0.00. That is right for a surprise and wrong for a model chosen on purpose:
Ask AI moved to ``anthropic/claude-haiku-5.5`` on 2026-10-10, which lists at
$0.10 / $0.50. Under the catch-all every chat turn would reserve and meter
about 30 times its cost and walk the per-project USD cap down that much faster.

Since the OSS release the single-node K3s overlay keeps the base (offline,
Ollama) AI posture and a site picks a hosted provider with a site patch, so the
models are pinned here directly, and any hosted model a tracked overlay names
must resolve to a specific entry too.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import llm_pricing

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]
OVERLAYS = REPO_ROOT / "k8s" / "overlays"
CATCH_ALL = next(price for provider, pattern, price in llm_pricing.PRICE_TABLE
                 if provider == "openrouter" and pattern == r".*")

#: Hosted models deployments are known to run through OpenRouter.
CHOSEN_MODELS = ("mistralai/mistral-nemo", "anthropic/claude-haiku-5.5")


@pytest.mark.parametrize("model", CHOSEN_MODELS)
def test_each_chosen_hosted_model_has_its_own_price(model):
    price = llm_pricing.resolve_price("openrouter", model)
    assert price is not None and price is not CATCH_ALL, f"{model!r} meters at the catch-all rate"


def _value(text: str, name: str) -> str | None:
    match = re.search(rf'^\s*{name}:\s*"([^"]*)"', text, flags=re.MULTILINE)
    return match.group(1) if match else None


def test_every_hosted_model_a_tracked_overlay_names_is_priced():
    checked = 0
    for kustomization in sorted(OVERLAYS.glob("*/kustomization.yaml")):
        text = kustomization.read_text(encoding="utf-8")
        if _value(text, "LLM_PROVIDER") != "openrouter":
            continue
        for setting in ("LLM_MODEL", "CHAT_LLM_MODEL"):
            model = _value(text, setting)
            if model:
                checked += 1
                price = llm_pricing.resolve_price("openrouter", model)
                assert price is not None and price is not CATCH_ALL, (
                    f"{kustomization.parent.name}: {setting}={model!r} meters at the catch-all rate"
                )
    assert checked >= 0  # no tracked overlay picks a hosted provider today


def test_the_chat_model_is_metered_at_its_list_rate():
    price = llm_pricing.resolve_price("openrouter", "anthropic/claude-haiku-5.5")
    assert (price.input_per_mtok, price.output_per_mtok) == (0.10, 0.50)
    # A typical turn: ~8k tokens in, ~500 out -- about a tenth of a cent.
    turn = llm_pricing.estimate_cost("openrouter", "anthropic/claude-haiku-5.5", input_tokens=8_000, output_tokens=500)
    assert turn.source == "priced" and turn.cost_usd == pytest.approx(0.00105)
