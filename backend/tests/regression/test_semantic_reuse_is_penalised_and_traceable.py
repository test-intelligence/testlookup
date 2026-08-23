"""Regression guard: a reused narrative is penalised and traceable (F-12).

The finding
-----------
The semantic cache returns the **full** cached analysis for any neighbour at
cosine similarity >= ``SEMANTIC_SIMILARITY_THRESHOLD`` (0.85) — root cause
summary, recommended actions, failure category and confidence. The caller then
clears ``evidence_references``.

So a narrative written about a *different* failure arrived asserting this test's
root cause, at the source test's **full confidence**, with nothing cited that
could contradict it. At the 0.85 threshold that accepts up to 15% dissimilarity
at zero confidence cost.

Why the existing metrics could not catch it
-------------------------------------------
The 2026-08-23 grounding readings — citation rate 76.5%, fabricated evidence IDs
0 — measure whether citations *resolve*, not whether the prose *belongs to this
test*. A borrowed narrative with its evidence cleared passes both. That is
precisely the shape of a confident wrong answer, and ``unsupported_claim_rate``
(the metric that would catch it) is still blocked on F-11.

What is guarded
---------------
* confidence is scaled by how far the match actually is, so a distant neighbour
  cannot speak with a near neighbour's authority;
* the original confidence is preserved, so the penalty is auditable rather than
  silently destructive;
* the source test is named, so a borrowed narrative is traceable to its origin;
* anything short of a near-exact match is handed to a human;
* a near-exact match is **not** penalised into uselessness — the cache has to
  stay worth having.

This deliberately does not stop reuse. Whether a 0.85 neighbour's prose should
be reused at all is a product decision, not a bug fix.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("asyncpg")

from app.services import semantic_cache  # noqa: E402
from app.services.semantic_cache import (  # noqa: E402
    NEAR_EXACT_REUSE_SIMILARITY,
    semantic_cache_lookup,
)

_SOURCE = {
    "root_cause_summary": "The checkout service returned a null cart.",
    "failure_category": "PRODUCT_BUG",
    "confidence_score": 90,
    "recommended_actions": ["Check recent commits"],
    "requires_human_review": False,
}


def _install_fake_chroma(monkeypatch, *, similarity: float, source_test: str = "test_checkout_null_cart"):
    """A collection that returns one neighbour at the given similarity."""
    distance = (1.0 - similarity) * 2.0  # inverse of the module's conversion

    class _Collection:
        def query(self, **_kw):
            return {
                "ids": [["doc-1"]],
                "distances": [[distance]],
                "metadatas": [[{
                    "analysis_json": json.dumps(_SOURCE),
                    "test_name": source_test,
                    "failure_category": "PRODUCT_BUG",
                    "confidence_score": "90",
                }]],
            }

    async def _get_or_create_collection(*_a, **_kw):
        return _Collection()

    monkeypatch.setattr(
        semantic_cache, "_get_or_create_collection", _get_or_create_collection
    )


async def _lookup() -> dict[str, Any] | None:
    return await semantic_cache_lookup(
        "test_payments_timeout", "TimeoutError: gateway did not respond", "", project_id="p-1",
    )


# ── The penalty scales with distance ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_distant_neighbour_cannot_speak_with_full_confidence(monkeypatch):
    """0.85 is the threshold — the loosest match the cache will serve."""
    _install_fake_chroma(monkeypatch, similarity=0.85)
    result = await _lookup()

    assert result is not None, "0.85 is at the threshold and must still hit"
    assert result["confidence_score"] < _SOURCE["confidence_score"], (
        "a 15%-dissimilar narrative must not inherit the source's confidence"
    )
    assert result["confidence_score"] == round(90 * 0.85)


@pytest.mark.asyncio
async def test_the_penalty_is_proportional_not_flat(monkeypatch):
    """A closer match must keep more confidence than a distant one."""
    _install_fake_chroma(monkeypatch, similarity=0.86)
    near = (await _lookup())["confidence_score"]
    _install_fake_chroma(monkeypatch, similarity=0.99)
    nearer = (await _lookup())["confidence_score"]

    assert nearer > near, "confidence must track how far the match actually is"


@pytest.mark.asyncio
async def test_a_near_exact_match_stays_useful(monkeypatch):
    """The cache still has to be worth having."""
    _install_fake_chroma(monkeypatch, similarity=1.0)
    result = await _lookup()

    assert result["confidence_score"] == _SOURCE["confidence_score"]
    assert result.get("requires_human_review") is False


# ── The penalty is auditable, and the prose is traceable ─────────────────────


@pytest.mark.asyncio
async def test_the_original_confidence_is_preserved(monkeypatch):
    """A silent overwrite would make the penalty unreviewable."""
    _install_fake_chroma(monkeypatch, similarity=0.90)
    result = await _lookup()

    assert result["confidence_score_before_reuse"] == 90


@pytest.mark.asyncio
async def test_the_borrowed_narrative_names_its_source(monkeypatch):
    """Without this, prose about another failure is untraceable."""
    _install_fake_chroma(monkeypatch, similarity=0.90, source_test="test_checkout_null_cart")
    result = await _lookup()

    assert result["semantic_source_test"] == "test_checkout_null_cart"
    assert result["semantic_similarity"] == 0.9
    assert result["semantic_cache_hit"] is True


@pytest.mark.asyncio
async def test_an_inexact_reuse_is_handed_to_a_human(monkeypatch):
    _install_fake_chroma(monkeypatch, similarity=0.90)
    result = await _lookup()

    assert result["requires_human_review"] is True
    assert 0.90 < NEAR_EXACT_REUSE_SIMILARITY


# ── Below the threshold nothing is served at all ─────────────────────────────


@pytest.mark.asyncio
async def test_below_the_threshold_there_is_no_hit(monkeypatch):
    _install_fake_chroma(monkeypatch, similarity=0.50)

    assert await _lookup() is None
