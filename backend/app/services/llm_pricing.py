"""Token → USD pricing, and honest capture of what a call actually used.

Phase 0 of the Batch-API PRD. Before this module TestLookup could not measure
LLM cost **at all**: there was not one price constant in the backend, and every
``cost_usd`` traced back to a literal ``0.0`` with a comment promising that
"cloud cost lands via base metering when known". It never did. The consequence
was that ``llm_cost_budget`` — 24 tests, a per-project ``hard_cap_usd``, a
whole settings page — metered **$0.00 for every call**, so the USD cap could
never trip, and no cost-reduction work could be justified or proved.

Two responsibilities, kept together because they are always used together:

1. **A price table.** Per provider and model, USD per million tokens, with
   separate input / output / cached-input rates and the Batch-API multiplier.
2. **Usage extraction.** Reading ``usage_metadata`` off a LangChain response
   *including the input/output split*, falling back to a marked estimate.

Design decisions worth keeping
------------------------------

**Self-hosted providers cost $0.00, and that is a real answer, not a missing
one.** Ollama / LM Studio / LocalAI / vLLM bill no per-token charge; the cost is
hardware. They return ``0.0`` with ``source="self_hosted"`` so a reader can tell
"free by nature" from "we don't know".

**An unknown cloud model is loud, not silent.** Returning a quiet ``0.0`` for a
model we have no price for is exactly the failure this module exists to end. An
unpriced cloud call logs a warning and increments
``llm_unpriced_calls_total`` so it shows up in monitoring instead of
understating the bill.

**Prices are data with a date, not truths.** ``PRICE_TABLE_UPDATED`` is exported
and surfaced by the metering API so a stale table is visible rather than
believed. Rates change; nothing here should be read as a current quote.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal, Optional

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

#: When the rates below were last checked against published rate cards.
#: Surfaced through the metering API — a stale table should be visible.
PRICE_TABLE_UPDATED = "2026-08-09"

#: Providers that run on hardware you already pay for. $0.00 per token is the
#: correct answer for these, not an unknown one.
SELF_HOSTED_PROVIDERS = frozenset({"ollama", "lmstudio", "localai", "vllm"})

#: The Batch API bills input and output at half the standard rate.
BATCH_DISCOUNT_MULTIPLIER = 0.5


@dataclass(frozen=True)
class ModelPrice:
    """USD per **one million** tokens."""

    input_per_mtok: float
    output_per_mtok: float
    #: Cache *reads* are heavily discounted; cache *writes* carry a premium.
    #: Both default to the plain input rate so an entry that omits them is
    #: merely imprecise rather than wrong in the cheap direction.
    cached_input_per_mtok: Optional[float] = None
    cache_write_per_mtok: Optional[float] = None

    def cached_read_rate(self) -> float:
        return (
            self.cached_input_per_mtok
            if self.cached_input_per_mtok is not None
            else self.input_per_mtok
        )

    def cache_write_rate(self) -> float:
        return (
            self.cache_write_per_mtok
            if self.cache_write_per_mtok is not None
            else self.input_per_mtok
        )


# ── Price table ───────────────────────────────────────────────────────────────
#
# Keys are ``(provider, model-name regex)``. The regex is matched against the
# lower-cased model string, first match wins, so order is significant: put the
# specific patterns above the general ones.
#
# These are list rates for reference only. Enterprise agreements, regional
# pricing and promotional rates all differ — override per deployment via
# ``LLM_PRICE_OVERRIDES`` rather than editing this table.

PRICE_TABLE: list[tuple[str, str, ModelPrice]] = [
    # ── Anthropic ────────────────────────────────────────────────────────────
    ("anthropic", r"opus",   ModelPrice(15.00, 75.00, cached_input_per_mtok=1.50,  cache_write_per_mtok=18.75)),
    ("anthropic", r"sonnet", ModelPrice(3.00,  15.00, cached_input_per_mtok=0.30,  cache_write_per_mtok=3.75)),
    ("anthropic", r"haiku",  ModelPrice(0.80,   4.00, cached_input_per_mtok=0.08,  cache_write_per_mtok=1.00)),
    # ── OpenAI ───────────────────────────────────────────────────────────────
    ("openai",    r"gpt-4o-mini", ModelPrice(0.15,  0.60, cached_input_per_mtok=0.075)),
    ("openai",    r"gpt-4o",      ModelPrice(2.50, 10.00, cached_input_per_mtok=1.25)),
    ("openai",    r"gpt-4",       ModelPrice(30.00, 60.00)),
    ("openai",    r"gpt-3\.5",    ModelPrice(0.50,  1.50)),
    # ── Google ───────────────────────────────────────────────────────────────
    ("gemini",    r"flash", ModelPrice(0.075, 0.30)),
    ("gemini",    r"pro",   ModelPrice(1.25,  5.00)),
]

CostSource = Literal["priced", "self_hosted", "unpriced", "no_tokens"]


@dataclass(frozen=True)
class CostEstimate:
    """What a call cost, and how much to trust that number."""

    cost_usd: float
    source: CostSource
    provider: str
    model: str
    #: True when the Batch-API discount was applied.
    batch: bool = False
    #: Set when ``source == "unpriced"`` — the reason there is no number.
    note: Optional[str] = None

    @property
    def is_known(self) -> bool:
        """False when the number understates reality rather than describing it."""
        return self.source in ("priced", "self_hosted", "no_tokens")


@lru_cache(maxsize=1)
def _overrides() -> list[tuple[str, str, ModelPrice]]:
    """Per-deployment rates from ``LLM_PRICE_OVERRIDES``.

    List rates rarely match what an organisation actually pays. The env var
    takes a JSON object keyed ``"provider:model-regex"``::

        {"anthropic:sonnet": {"input_per_mtok": 2.4, "output_per_mtok": 12.0}}

    Malformed JSON is logged and ignored rather than raised — a typo in an
    optional pricing override must not take the analysis pipeline down.
    """
    raw = getattr(settings, "LLM_PRICE_OVERRIDES", None)
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else dict(raw)
        out: list[tuple[str, str, ModelPrice]] = []
        for key, value in parsed.items():
            provider, _, pattern = str(key).partition(":")
            out.append(
                (
                    provider.strip().lower(),
                    pattern.strip().lower() or ".*",
                    ModelPrice(
                        input_per_mtok=float(value["input_per_mtok"]),
                        output_per_mtok=float(value["output_per_mtok"]),
                        cached_input_per_mtok=(
                            float(value["cached_input_per_mtok"])
                            if value.get("cached_input_per_mtok") is not None
                            else None
                        ),
                        cache_write_per_mtok=(
                            float(value["cache_write_per_mtok"])
                            if value.get("cache_write_per_mtok") is not None
                            else None
                        ),
                    ),
                )
            )
        return out
    except Exception as exc:  # noqa: BLE001 — bad config must not break analysis
        logger.warning("llm_price_overrides_invalid", error=str(exc))
        return []


def reset_price_cache() -> None:
    """Drop the memoised overrides. For tests and config reloads."""
    _overrides.cache_clear()


def resolve_price(provider: str, model: str) -> Optional[ModelPrice]:
    """Look up the rate for a provider/model, or None when it is not known.

    Deployment overrides win over the built-in table; within each, the first
    matching pattern wins, so specific entries must precede general ones.
    """
    p = (provider or "").strip().lower()
    m = (model or "").strip().lower()
    for table_provider, pattern, price in [*_overrides(), *PRICE_TABLE]:
        if table_provider == p and re.search(pattern, m):
            return price
    return None


def estimate_cost(
    provider: str,
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
    batch: bool = False,
) -> CostEstimate:
    """Price a single LLM call.

    ``cached_input_tokens`` are billed at the cache-read rate and must be
    **excluded** from ``input_tokens`` by the caller — passing the same tokens
    in both would double-bill them.
    """
    p = (provider or "").strip().lower()
    total_tokens = input_tokens + output_tokens + cached_input_tokens + cache_write_tokens

    if total_tokens <= 0:
        return CostEstimate(0.0, "no_tokens", p, model)

    if p in SELF_HOSTED_PROVIDERS:
        # Not unknown — genuinely no per-token charge.
        return CostEstimate(0.0, "self_hosted", p, model)

    price = resolve_price(p, model)
    if price is None:
        # Loud on purpose: a silent zero here is the defect this module exists
        # to end. Understating the bill must never be the quiet default.
        logger.warning(
            "llm_call_unpriced",
            provider=p,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            hint="add a PRICE_TABLE entry or set LLM_PRICE_OVERRIDES",
        )
        _record_unpriced(p, model)
        return CostEstimate(
            0.0,
            "unpriced",
            p,
            model,
            batch=batch,
            note=f"no price entry for provider={p!r} model={model!r}",
        )

    cost = (
        input_tokens / 1e6 * price.input_per_mtok
        + output_tokens / 1e6 * price.output_per_mtok
        + cached_input_tokens / 1e6 * price.cached_read_rate()
        + cache_write_tokens / 1e6 * price.cache_write_rate()
    )
    if batch:
        cost *= BATCH_DISCOUNT_MULTIPLIER

    return CostEstimate(round(cost, 8), "priced", p, model, batch=batch)


def _record_unpriced(provider: str, model: str) -> None:
    """Increment the unpriced-call counter, if metrics are wired."""
    try:
        from app.core.metrics import llm_unpriced_calls_total  # noqa: PLC0415

        llm_unpriced_calls_total.labels(provider=provider, model=model or "unknown").inc()
    except Exception:  # noqa: BLE001 — metering must never break a call path
        pass


# ── Usage extraction ──────────────────────────────────────────────────────────

UsageSource = Literal["reported", "estimated", "none"]

#: Rough characters-per-token for the fallback estimate. Only used when the
#: provider reports nothing; an estimate is always marked as one.
_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class TokenUsage:
    """Tokens a call used, with the input/output split preserved.

    The split matters: output tokens cost several times more than input on
    every cloud provider, so collapsing both into a total — which every call
    site in this codebase used to do — cannot be priced correctly.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    source: UsageSource = "none"
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cached_input_tokens
            + self.cache_write_tokens
        )


def _estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // _CHARS_PER_TOKEN)


def extract_token_usage(
    response: Any,
    *,
    fallback_prompt: str = "",
    fallback_completion: str = "",
) -> TokenUsage:
    """Read token usage off a LangChain response, preserving the split.

    Falls back to a character-length estimate when the provider reports
    nothing, and marks it ``source="estimated"`` so a caller can tell a
    measurement from a guess.
    """
    usage = getattr(response, "usage_metadata", None) or {}

    if isinstance(usage, dict) and usage:
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)

        # Cache counters live in a nested dict on LangChain's normalised shape.
        details = usage.get("input_token_details") or {}
        cached = int(details.get("cache_read") or 0)
        cache_write = int(details.get("cache_creation") or 0)
        # LangChain reports cache reads *inside* input_tokens; bill them once.
        input_tokens = max(0, input_tokens - cached - cache_write)

        if input_tokens or output_tokens or cached or cache_write:
            return TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached,
                cache_write_tokens=cache_write,
                source="reported",
                details=dict(usage),
            )

        # Some providers report only a total. Attribute it to input rather
        # than splitting it arbitrarily — input is the cheaper rate, so this
        # under-states rather than inventing an output count that never
        # existed. The estimate below is preferred when text is available.
        total = int(usage.get("total_tokens") or 0)
        if total and not (fallback_prompt or fallback_completion):
            return TokenUsage(input_tokens=total, source="reported", details=dict(usage))

    if fallback_prompt or fallback_completion:
        return TokenUsage(
            input_tokens=_estimate_tokens(fallback_prompt),
            output_tokens=_estimate_tokens(fallback_completion),
            source="estimated",
        )

    return TokenUsage(source="none")
