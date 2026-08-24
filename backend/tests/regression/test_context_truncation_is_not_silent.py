"""Regression guard: dropping context is counted, not silent (F-5).

The finding
-----------
Two ways evidence disappeared without anyone being told:

1. **Our own truncation was unrecorded.** ``truncate_to_token_budget`` returned
   a bare string, so a caller could not tell a truncated context from an intact
   one. Six call sites truncate; **none** recorded it. The only trace was a
   marker appended *into the prompt*, which reaches the model rather than any
   metric.

2. **The estimate was optimistic.** ``len(text) // 4`` is the English figure and
   is wrong for what this pipeline sends -- stack traces, JSON and camelCase
   identifiers tokenize nearer 2.5-3 chars/token. Under-counting lets an
   over-length prompt through, and the provider then truncates it server-side
   with no error, taking evidence and (on the ReAct path) the tool instructions
   with it. That surfaces later as a parse failure with no stated cause.

3. **``num_ctx`` was never set for Ollama**, so Ollama applied its own default
   (2048 on many builds) and truncated from the left. ``num_predict`` caps
   *output*; one number cannot do both jobs.

What is guarded
---------------
* the estimate errs conservative, so we truncate visibly rather than letting the
  provider truncate silently;
* a truncation reports what it dropped, and an intact context reports nothing;
* the summary layer path records a truncation as a decision, where the baseline
  harness already reads;
* Ollama receives an explicit context window, distinct from the output cap.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("asyncpg")

from app.services.resilience import (  # noqa: E402
    CHARS_PER_TOKEN,
    TRUNCATION_CHARS_PER_TOKEN,
    estimate_token_count,
    truncate_to_token_budget,
    truncate_with_report,
)


# ── The estimate errs toward safety ──────────────────────────────────────────


def test_truncation_is_stricter_than_accounting():
    """The two want opposite biases, so they cannot share one constant.

    Accounting drives cost and tool/reservation budgets -- over-estimating makes
    the system do LESS work (CI caught the copilot exhausting after one tool
    call instead of two). Truncation is the opposite: under-estimating lets an
    over-length prompt through and the provider truncates it server-side with no
    error. Ours is logged, theirs is not.
    """
    assert TRUNCATION_CHARS_PER_TOKEN < CHARS_PER_TOKEN, (
        "truncation must err low, or an over-length prompt reaches the provider"
    )


def test_accounting_is_unchanged_so_budgets_do_not_shrink():
    """Inflating this silently reduces how much work the agents will do."""
    assert CHARS_PER_TOKEN == 4
    assert estimate_token_count("a" * 4000) == 1000


def test_truncation_cuts_earlier_than_accounting_would_allow():
    budget = 100
    report = truncate_with_report("z" * (budget * CHARS_PER_TOKEN), budget)

    assert report.truncated, (
        "a prompt the accounting ratio calls in-budget must still be trimmed"
    )


# ── Truncation reports what it dropped ───────────────────────────────────────


def test_a_truncated_context_says_so():
    report = truncate_with_report("y" * 900, 100, label="incident_view")

    assert report.truncated is True
    assert report.original_chars == 900
    assert report.dropped_chars == 900 - (100 * TRUNCATION_CHARS_PER_TOKEN)
    assert 0 < report.dropped_fraction <= 1


def test_an_intact_context_reports_no_loss():
    report = truncate_with_report("short", 100)

    assert report.truncated is False
    assert report.dropped_chars == 0
    assert report.dropped_fraction == 0.0
    assert report.text == "short", "an in-budget context must be passed through whole"


def test_the_marker_is_appended_only_when_truncating():
    truncated = truncate_with_report("z" * 900, 10).text
    intact = truncate_with_report("z" * 5, 10).text

    assert "truncated to fit token budget" in truncated
    assert "truncated to fit token budget" not in intact


def test_the_legacy_helper_still_returns_just_text():
    """Six call sites use it; the signature must not break under them."""
    out = truncate_to_token_budget("w" * 900, 10)

    assert isinstance(out, str)
    assert len(out) < 900


def test_dropped_fraction_survives_an_empty_input():
    report = truncate_with_report("", 10)

    assert report.dropped_fraction == 0.0


# ── The loss reaches the decision log ────────────────────────────────────────


def test_the_summary_layer_records_a_truncation():
    """Where the baseline harness already reads. Without this the loss is
    invisible to every metric, which is what made F-5 'silent'."""
    from app.agents.summary_agent import SummaryAgent

    src = inspect.getsource(SummaryAgent._call_json_layer)

    assert "truncate_with_report" in src, "the layer path must use the reporting form"
    assert 'decision_point="context_truncated"' in src, (
        "a dropped context that nothing counts is exactly the defect"
    )
    assert "report.truncated" in src, "it must record only when it actually truncated"


# ── Ollama gets an explicit context window ───────────────────────────────────


def test_ollama_receives_an_explicit_context_window():
    from app.services import llm_factory

    src = inspect.getsource(llm_factory.get_llm)

    assert "num_ctx=settings.OLLAMA_NUM_CTX" in src, (
        "unset num_ctx lets Ollama truncate from the left with no error"
    )


def test_the_context_window_is_not_the_output_cap():
    """num_predict caps output; conflating them is how the window went unset."""
    from app.core.config import settings

    assert hasattr(settings, "OLLAMA_NUM_CTX")
    assert settings.OLLAMA_NUM_CTX > settings.LLM_MAX_TOKENS, (
        "the context window must exceed the output cap or long prompts truncate"
    )
