"""A cloud LLM call must produce a non-zero ``cost_usd``.

Before this, TestLookup could not measure LLM cost at all. There was not one
token-price constant in the backend, and every ``cost_usd`` traced back to a
literal ``0.0`` — including the one carrying the comment "cloud cost lands via
base metering when known", which it never did.

The consequence was not merely a missing number. ``llm_cost_budget`` — 24
tests, a per-project ``hard_cap_usd``, an admin settings page — metered
**$0.00 for every call**, so the USD cap could never trip, and no
cost-reduction work (batching, caching, dedup) could be justified or proved.

These tests pin the property that was absent, not one worked example:

* a priced cloud call costs **more than zero**
* output tokens cost **more than** the same number of input tokens, because a
  collapsed total — which every call site used to report — cannot be priced
* self-hosted $0.00 is **distinguishable** from unpriced $0.00
* an unpriced cloud model is **loud**, never a silent understatement
"""
from __future__ import annotations

import pytest

pytest.importorskip("app.services.llm_pricing")

from app.services.llm_pricing import (  # noqa: E402
    BATCH_DISCOUNT_MULTIPLIER,
    PRICE_TABLE,
    SELF_HOSTED_PROVIDERS,
    estimate_cost,
    extract_token_usage,
    resolve_price,
)

pytestmark = pytest.mark.regression


class TestCloudCallsAreCosted:
    def test_a_cloud_call_costs_more_than_zero(self):
        est = estimate_cost(
            "anthropic", "claude-sonnet-4-5", input_tokens=10_000, output_tokens=2_000
        )
        assert est.cost_usd > 0, (
            "a cloud LLM call metered $0.00 — this is the defect the price "
            "table exists to fix; llm_cost_budget's USD cap cannot trip while "
            "every call costs nothing"
        )
        assert est.source == "priced"
        assert est.is_known

    @pytest.mark.parametrize(
        "provider,model",
        # Derive a sample model name from each pattern by dropping the regex
        # escapes (``gpt-3\.5`` -> ``gpt-3.5``), so the cases still come from
        # the table and a new entry is covered automatically.
        [(p, pattern.replace("\\", "")) for p, pattern, _ in PRICE_TABLE],
    )
    def test_every_table_entry_prices_a_call(self, provider: str, model: str):
        """Derived from the table, so a malformed new entry fails here."""
        est = estimate_cost(provider, model, input_tokens=1000, output_tokens=1000)
        assert est.source == "priced", f"{provider}/{model} did not resolve to a price"
        assert est.cost_usd > 0

    def test_output_tokens_cost_more_than_input(self):
        """Why the input/output split has to be preserved.

        Every call site used to report only ``total_tokens`` and pass it as
        ``input_tokens``. On every cloud provider output is several times
        dearer, so a collapsed total systematically under-states the bill.
        """
        as_input = estimate_cost("anthropic", "claude-sonnet-4-5", input_tokens=100_000)
        as_output = estimate_cost("anthropic", "claude-sonnet-4-5", output_tokens=100_000)
        assert as_output.cost_usd > as_input.cost_usd

    def test_arithmetic_is_per_million_tokens(self):
        price = resolve_price("anthropic", "claude-sonnet-4-5")
        assert price is not None
        est = estimate_cost("anthropic", "claude-sonnet-4-5", input_tokens=1_000_000)
        assert est.cost_usd == pytest.approx(price.input_per_mtok)


class TestZeroMeansDifferentThings:
    """A $0.00 that is correct and a $0.00 that is ignorance must not look alike."""

    @pytest.mark.parametrize("provider", sorted(SELF_HOSTED_PROVIDERS))
    def test_self_hosted_is_free_and_says_so(self, provider: str):
        est = estimate_cost(provider, "llama3", input_tokens=50_000, output_tokens=10_000)
        assert est.cost_usd == 0.0
        assert est.source == "self_hosted"
        assert est.is_known, "self-hosted $0.00 is an answer, not a gap"

    def test_an_unpriced_cloud_model_is_not_silently_free(self):
        est = estimate_cost("openai", "some-unreleased-model", input_tokens=1_000_000)
        assert est.source == "unpriced"
        assert not est.is_known, (
            "an unpriced cloud call reported itself as a known $0.00 — that is "
            "exactly how the bill gets under-stated invisibly"
        )
        assert est.note

    def test_no_tokens_is_not_unpriced(self):
        est = estimate_cost("anthropic", "claude-sonnet-4-5")
        assert est.source == "no_tokens"
        assert est.is_known


class TestBatchDiscount:
    def test_batch_halves_the_cost(self):
        std = estimate_cost("anthropic", "claude-sonnet-4-5", input_tokens=10_000, output_tokens=5_000)
        bat = estimate_cost(
            "anthropic", "claude-sonnet-4-5", input_tokens=10_000, output_tokens=5_000, batch=True
        )
        assert bat.cost_usd == pytest.approx(std.cost_usd * BATCH_DISCOUNT_MULTIPLIER)
        assert bat.batch is True

    def test_batch_does_not_make_a_self_hosted_call_negative(self):
        est = estimate_cost("ollama", "llama3", input_tokens=10_000, batch=True)
        assert est.cost_usd == 0.0


class TestCachedTokensAreBilledOnce:
    def test_cached_input_is_cheaper_than_fresh_input(self):
        fresh = estimate_cost("anthropic", "claude-sonnet-4-5", input_tokens=100_000)
        cached = estimate_cost("anthropic", "claude-sonnet-4-5", cached_input_tokens=100_000)
        assert cached.cost_usd < fresh.cost_usd, (
            "cache reads must be discounted — this is the lever the PRD ranks "
            "above batching for the interactive path"
        )


class _Resp:
    def __init__(self, usage=None, content=""):
        if usage is not None:
            self.usage_metadata = usage
        self.content = content


class TestUsageExtraction:
    def test_reported_split_is_preserved(self):
        usage = extract_token_usage(_Resp({"input_tokens": 900, "output_tokens": 100}))
        assert (usage.input_tokens, usage.output_tokens) == (900, 100)
        assert usage.source == "reported"
        assert usage.total_tokens == 1000

    def test_cache_reads_are_not_double_billed(self):
        """LangChain reports cache reads *inside* input_tokens."""
        usage = extract_token_usage(
            _Resp(
                {
                    "input_tokens": 1000,
                    "output_tokens": 50,
                    "input_token_details": {"cache_read": 800},
                }
            )
        )
        assert usage.cached_input_tokens == 800
        assert usage.input_tokens == 200, "cached tokens were billed twice"
        assert usage.total_tokens == 1050

    def test_an_estimate_is_marked_as_one(self):
        usage = extract_token_usage(
            _Resp(None), fallback_prompt="x" * 400, fallback_completion="y" * 40
        )
        assert usage.source == "estimated", "a guess must not pass as a measurement"
        assert usage.input_tokens > usage.output_tokens > 0

    def test_nothing_reported_and_nothing_to_estimate(self):
        usage = extract_token_usage(_Resp(None))
        assert usage.source == "none"
        assert usage.total_tokens == 0


class TestTheFunnelPricesEveryStage:
    """``mark_stage_done`` is the single metering funnel every agent stage
    passes through. Pricing centrally there is what makes the meter cover the
    whole pipeline rather than the one or two call sites that remembered to
    compute a cost — which, before this, was none of them."""

    @pytest.mark.asyncio
    async def test_a_stage_reporting_tokens_gets_priced(self, monkeypatch):
        from app.agents.base import BaseAgent

        class _Stage(BaseAgent):
            stage_name = "pricing_probe"

            async def run(self, state):  # pragma: no cover — not exercised
                return {}

        async def _fake_config():
            return {"provider": "anthropic", "model": "claude-sonnet-4-5"}

        monkeypatch.setattr(
            "app.services.ai_config_resolver.get_effective_ai_config", _fake_config
        )

        est = await _Stage()._estimate_stage_cost(10_000, 2_000)
        assert est is not None
        assert est.cost_usd > 0, "the metering funnel still prices a cloud stage at $0.00"
        assert est.source == "priced"

    @pytest.mark.asyncio
    async def test_estimation_failure_never_breaks_a_stage(self, monkeypatch):
        """Metering is best-effort: a broken price lookup must not be able to
        fail test analysis."""
        from app.agents.base import BaseAgent

        class _Stage(BaseAgent):
            stage_name = "pricing_probe"

            async def run(self, state):  # pragma: no cover
                return {}

        def _explode(*_a, **_k):
            raise RuntimeError("pricing is down")

        monkeypatch.setattr("app.services.llm_pricing.estimate_cost", _explode)
        # Must return None rather than propagating.
        assert await _Stage()._estimate_stage_cost(1000, 100) is None
