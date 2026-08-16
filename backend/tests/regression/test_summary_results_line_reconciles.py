"""Regression: the results figures the summary states must add up.

Bug (homelab, 2026-08-16): the summary agent described a run as

    Results: 6 tests, 2 failures, 60.0% pass rate

which cannot be read consistently — 6 - 2 = 4 passed would be 66.7%. The
missing term is the skipped test: pass rate is ``passed / executed``, and the
line neither mentioned skips nor said what the rate was over.

Both consumers of that line were harmed. Handed figures that do not reconcile,
the LLM invented a count to make the arithmetic work — the live run produced
"Pass rate was 60.0% with 1 failure out of 6 tests" against ground truth of 3
passed / 1 failure / 1 error / 1 skipped. The deterministic fallback narrative
built the same contradiction from the same fields and showed it to the user
with no model involved at all.

The guard is the CLASS: a figure set published to a reader must reconcile, and
the basis of a derived rate must be stated rather than left to be inferred.
Both call sites are pinned, because fixing only the prompt would have left the
fallback — the path that runs precisely when the LLM is unavailable — still
printing the contradiction.

Ground truth used throughout is the real homelab fixture:
    6 tests, 3 passed, 1 failure + 1 error = 2 failed, 1 skipped, 60.0%.
"""
from __future__ import annotations

import re

RUN_DATA = {
    "total_tests": 6,
    "passed_tests": 3,
    "failed_tests": 2,
    "skipped_tests": 1,
    "pass_rate": 60.0,
    "build_number": "b-1",
    "branch": "main",
}


def _line(**overrides) -> str:
    from app.agents.summary_agent import _format_results_line

    return _format_results_line({**RUN_DATA, **overrides})


# ── The numbers themselves ──────────────────────────────────────────────────


def test_the_counts_stated_actually_sum_to_the_total():
    """Parse the numbers back out of the rendered line and check the arithmetic.

    Deliberately re-derived from the string rather than compared to a fixed
    sentence: this asserts the property that broke, so it keeps working if the
    wording changes.
    """
    line = _line()
    passed = int(re.search(r"(\d+) passed", line).group(1))
    failed = int(re.search(r"(\d+) failed", line).group(1))
    skipped = int(re.search(r"(\d+) skipped", line).group(1))
    total = int(re.search(r"(\d+) tests", line).group(1))

    assert passed + failed + skipped == total, (
        f"the stated counts do not sum to the stated total: {line!r}"
    )


def test_the_stated_rate_matches_the_stated_counts():
    """The exact inconsistency that made the model fabricate: the rate has to be
    recoverable from the counts on the same line."""
    line = _line()
    passed = int(re.search(r"(\d+) passed", line).group(1))
    skipped = int(re.search(r"(\d+) skipped", line).group(1))
    total = int(re.search(r"(\d+) tests", line).group(1))
    rate = float(re.search(r"Pass rate ([\d.]+)%", line).group(1))

    executed = total - skipped
    assert abs(passed / executed * 100 - rate) < 0.05, (
        f"pass rate {rate} is not passed/executed for the counts shown: {line!r}"
    )


def test_the_skipped_count_is_present():
    """Omitting it is what made the figures irreconcilable in the first place."""
    assert "1 skipped" in _line()


def test_the_basis_of_the_rate_is_stated():
    """A reader must not have to infer that skips are excluded."""
    assert "skipped excluded" in _line().lower()


# ── Honesty when the run does not carry the fields ──────────────────────────


def test_absent_counts_are_omitted_not_invented():
    """A count must never be derived by subtraction — inferring "passed" from the
    others would publish a number nobody measured, which is the same class of
    defect this line already had."""
    line = _line(passed_tests=None, skipped_tests=None)

    # Match the COUNT, not the bare word — "passed" and "skipped" both appear in
    # the basis clause "(passed / executed; skipped excluded)", so a substring
    # check here passes for the wrong reason and would never fail.
    assert not re.search(r"\d+ passed", line), (
        f"invented a passed count that was not supplied: {line!r}"
    )
    assert not re.search(r"\d+ skipped", line), (
        f"invented a skipped count that was not supplied: {line!r}"
    )
    assert "2 failed" in line, "dropped the count that WAS available"


# ── Both call sites must use it ─────────────────────────────────────────────


def test_the_llm_prompt_uses_the_reconciled_line():
    """The prompt is what the model reasons over; a contradiction here is what
    produced the fabricated failure count."""
    from app.agents.summary_agent import SummaryAgent

    context = SummaryAgent()._build_context(
        run_data=RUN_DATA, anomaly_summary="", anomalies=[], analyses={},
    )
    assert "1 skipped" in context and "skipped excluded" in context.lower(), (
        f"the LLM context still states figures that do not reconcile:\n{context[:400]}"
    )


def test_the_deterministic_fallback_uses_the_reconciled_line():
    """The fallback runs exactly when the LLM is unavailable, so it is the path a
    user is most likely to be reading when things are already going wrong."""
    from app.agents.summary_agent import SummaryAgent

    result = SummaryAgent()._build_fallback_structured_report(
        run_data=RUN_DATA, anomaly_summary="", anomalies=[], analyses={},
    )
    text = str(result.get("layer1_executive_summary", ""))

    assert "1 skipped" in text and "skipped excluded" in text.lower(), (
        f"the fallback narrative still states figures that do not reconcile: {text!r}"
    )
