"""Regression guard: the fast classifier's failures are no longer silent.

The defect
----------
``FastClassifier.classify`` returned a bare ``None`` for **four** different
outcomes — a provider error, unparseable output, a low-confidence abstention,
and success-with-no-result. The caller could not tell them apart, so nothing
was ever recorded.

That made it the last unmeasured LLM path in the pipeline. The baseline harness
reported ``root_cause_analysis: 294 llm_calls, 0 parse_failures``, and the Mongo
event log held **zero** ``schema_validation_failed`` events across 9,657 — not
because there were none, but because no code path could emit one.

The distinction that matters
----------------------------
``low_confidence`` is **not a failure**. It is the classifier correctly handing
a hard case to the ReAct loop. Counting it would inflate the exact metric this
instrumentation exists to make trustworthy — so it is logged under a separate
decision point and excluded from the harness's parse-failure count.

What is guarded
---------------
* each outcome is reported distinctly rather than collapsing to ``None``;
* a parse failure is counted; an abstention is not;
* the legacy ``classify()`` signature still returns just the verdict, so
  existing callers are unaffected.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")

_BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "pipeline"
sys.path.insert(0, str(_BENCH))

from aggregate import _PARSE_FAILURE_POINTS  # noqa: E402
from app.services.training.classifier import FastClassifier  # noqa: E402

# The module object FastClassifier executes in (re-audit E2). A dotted-path
# monkeypatch resolves through sys.modules / the package attribute at patch
# time; if another test re-imported the module, that is a different object
# and the patch silently misses, so the real LLM path ran.
_CLASSIFIER_MODULE = sys.modules[FastClassifier.__module__]


class _Resp:
    def __init__(self, content):
        self.content = content


def _patch_llm(monkeypatch, response):
    async def _fake_get_llm(*_a, **_kw):
        class _LLM:
            async def ainvoke(self, *_args, **_kwargs):
                if isinstance(response, Exception):
                    raise response
                return _Resp(response)
        return _LLM()

    monkeypatch.setattr(_CLASSIFIER_MODULE, "get_llm", _fake_get_llm)
    monkeypatch.setattr(
        _CLASSIFIER_MODULE.ModelRegistry,
        "get_active_model",
        AsyncMock(return_value=None),
    )


# ── Each outcome is distinguishable ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_unparseable_output_reports_parse_failed(monkeypatch):
    """The case the bare None hid: the model answered, unreadably."""
    _patch_llm(monkeypatch, "I think this is probably an infrastructure problem.")
    result, outcome = await FastClassifier.classify_with_outcome(
        test_name="t", error_message="boom",
    )

    assert result is None
    assert outcome == "parse_failed"


@pytest.mark.asyncio
async def test_a_provider_error_reports_call_failed(monkeypatch):
    _patch_llm(monkeypatch, RuntimeError("connection reset"))
    result, outcome = await FastClassifier.classify_with_outcome(
        test_name="t", error_message="boom",
    )

    assert result is None
    assert outcome == "call_failed"


@pytest.mark.asyncio
async def test_a_low_confidence_answer_is_an_abstention_not_a_failure(monkeypatch):
    _patch_llm(monkeypatch, '{"category": "FLAKY", "confidence": 10, "reasoning": "unsure"}')
    result, outcome = await FastClassifier.classify_with_outcome(
        test_name="t", error_message="boom",
    )

    assert result is None
    assert outcome == "low_confidence", "an abstention must not read as a failure"


@pytest.mark.asyncio
async def test_a_confident_answer_reports_classified(monkeypatch):
    _patch_llm(
        monkeypatch,
        '{"category": "INFRASTRUCTURE", "confidence": 95, "reasoning": "connection refused"}',
    )
    result, outcome = await FastClassifier.classify_with_outcome(
        test_name="t", error_message="Connection refused",
    )

    assert outcome == "classified"
    assert result["failure_category"] == "INFRASTRUCTURE"
    assert result["classified_by"] == "fast_classifier"


# ── The harness counts the right thing ───────────────────────────────────────


def test_the_harness_counts_parse_failures_but_not_abstentions():
    """An abstention is the classifier working; counting it would inflate the
    rate this instrumentation exists to make trustworthy."""
    assert "classifier_schema_validation" in _PARSE_FAILURE_POINTS
    assert "classifier_call_outcome" not in _PARSE_FAILURE_POINTS


# ── Backwards compatibility ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_legacy_signature_still_returns_just_the_verdict(monkeypatch):
    """analysis_agent's progressive fallback still calls classify()."""
    _patch_llm(
        monkeypatch,
        '{"category": "PRODUCT_BUG", "confidence": 92, "reasoning": "npe"}',
    )
    result = await FastClassifier.classify(test_name="t", error_message="npe")

    assert isinstance(result, dict)
    assert result["failure_category"] == "PRODUCT_BUG"


@pytest.mark.asyncio
async def test_the_legacy_signature_still_returns_none_on_failure(monkeypatch):
    _patch_llm(monkeypatch, "not json at all")
    assert await FastClassifier.classify(test_name="t", error_message="boom") is None
