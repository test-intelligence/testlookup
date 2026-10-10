"""The homelab's models have their own price entries, not OpenRouter's catch-all.

``llm_pricing`` prices an unrecognised OpenRouter model at a deliberately high
catch-all ($3 / $15 per million tokens), so an unknown model never meters
$0.00. That is right for a surprise and wrong for a model chosen on purpose:
Ask AI moved to ``anthropic/claude-haiku-5.5`` on 2026-10-10 (CHAT_LLM_MODEL in
the homelab overlay), which lists at $0.10 / $0.50. Under the catch-all every
chat turn would reserve and meter about 30 times its cost and walk the
per-project USD cap down that much faster.

A model named in the overlay must therefore resolve to a specific entry.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import llm_pricing

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]
OVERLAY = REPO_ROOT / "k8s" / "overlays" / "homelab" / "kustomization.yaml"
CATCH_ALL = next(price for provider, pattern, price in llm_pricing.PRICE_TABLE
                 if provider == "openrouter" and pattern == r".*")


def _overlay_value(name: str) -> str:
    match = re.search(rf'^\s*{name}:\s*"([^"]*)"', OVERLAY.read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert match, f"{name} is not set in {OVERLAY.name}"
    return match.group(1)


@pytest.mark.parametrize("setting", ["LLM_MODEL", "CHAT_LLM_MODEL"])
def test_each_homelab_model_has_its_own_price(setting):
    if not OVERLAY.exists():
        pytest.skip("homelab overlay not present in this checkout")
    assert _overlay_value("LLM_PROVIDER") == "openrouter"
    model = _overlay_value(setting)
    price = llm_pricing.resolve_price("openrouter", model)
    assert price is not None and price is not CATCH_ALL, f"{setting}={model!r} meters at the catch-all rate"


def test_the_chat_model_is_metered_at_its_list_rate():
    price = llm_pricing.resolve_price("openrouter", "anthropic/claude-haiku-5.5")
    assert (price.input_per_mtok, price.output_per_mtok) == (0.10, 0.50)
    # A typical turn: ~8k tokens in, ~500 out -- about a tenth of a cent.
    turn = llm_pricing.estimate_cost("openrouter", "anthropic/claude-haiku-5.5", input_tokens=8_000, output_tokens=500)
    assert turn.source == "priced" and turn.cost_usd == pytest.approx(0.00105)
