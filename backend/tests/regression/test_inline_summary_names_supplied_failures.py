"""W23c F-1, part 2 (F-OR07-1): an inline summary's data names the supplied failures and states no build pass rate.

``summary_system`` v2 (#324) tells the model that a ``<<data>>`` block's counts describe only the supplied
failures. On the reference K3s install (2026-11-04, invocation 35d5151d, live OpenRouter model) the draft STILL read "The test
run had a 0.0% pass rate with 1 failed test out of 1 executed … the release is not ready": the context's first
lines were ``Build: … Results: 1 tests — 1 failed. Pass rate 0.0%``, the inline seed's figures stated flatly as
the build's result, and the small model followed the data over the rule. The fix is in the data: for an inline
seed the results line names the supplied failures and carries no pass rate, and the deterministic fallback
derives no release signal from them. A stored run's line is unchanged.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression


def _inline_seed(n: int = 1) -> dict:
    from app.services.inline_invocation import prepare, seed_state

    payload, _sha = prepare({"schema_version": 1, "failures": [
        {"test_name": f"checkout total {i}", "suite_name": "cart", "failure_category": "INFRASTRUCTURE",
         "confidence_score": 70, "root_cause": "the health endpoint timed out", "log_excerpt": "GET /health 503"}
        for i in range(n)
    ]})
    return seed_state(payload, build_number="or07a-walk-1", branch="main")


def test_the_inline_results_line_names_the_supplied_failures_and_has_no_pass_rate():
    from app.agents.summary_agent import _format_results_line

    line = _format_results_line(_inline_seed(1)["test_run_data"])
    assert line.startswith("Supplied failure: 1, sent by the caller with this request.")
    assert "not this build's results" in line
    assert "%" not in line and "Pass rate" not in line and "Results:" not in line
    assert _format_results_line(_inline_seed(3)["test_run_data"]).startswith("Supplied failures: 3,")


def test_a_stored_runs_results_line_is_unchanged():
    from app.agents.summary_agent import _format_results_line

    line = _format_results_line({"total_tests": 6, "failed_tests": 1, "passed_tests": 4, "skipped_tests": 1,
                                 "pass_rate": 80.0, "status": "FAILED"})
    assert line == "Results: 6 tests — 4 passed, 1 failed, 1 skipped. Pass rate 80.0% (passed / executed; skipped excluded)."


def test_the_rendered_inline_context_states_no_build_figure():
    """What the model reads: the context carries the supplied-failures line and no percentage at all."""
    from app.agents.summary_agent import SummaryAgent
    from app.services.evidence_catalogue import build_evidence_catalogue

    seed = _inline_seed(1)
    agent = object.__new__(SummaryAgent)
    context = agent._build_context(
        seed["test_run_data"], "", [], seed["analyses"],
        similar_failures=[], catalogue=build_evidence_catalogue(seed["analyses"]),
    )
    assert "Supplied failure: 1, sent by the caller with this request." in context
    assert "Pass rate" not in context and "0.0%" not in context


def test_the_fallback_derives_no_release_signal_from_supplied_failures():
    from app.agents.summary_agent import SummaryAgent

    seed = _inline_seed(1)
    agent = object.__new__(SummaryAgent)
    report = agent._build_fallback_structured_report(seed["test_run_data"], "", [], seed["analyses"])
    sentence = report["layer1_executive_summary"]
    assert "Supplied failure: 1" in sentence
    assert "release signal" not in sentence and "0.0%" not in sentence
    hint = report["layer4_action_plan"]["owner_hints"]["release_manager"]
    assert hint.startswith("These are failures supplied with the request, not this run's results")


def test_a_stored_runs_fallback_still_states_its_release_signal():
    from app.agents.summary_agent import SummaryAgent

    agent = object.__new__(SummaryAgent)
    run_data = {"build_number": "b-1", "branch": "main", "total_tests": 10, "failed_tests": 5, "passed_tests": 5,
                "pass_rate": 50.0, "status": "FAILED"}
    report = agent._build_fallback_structured_report(run_data, "", [], {})
    assert "the current release signal is NO GO" in report["layer1_executive_summary"]
    assert report["layer4_action_plan"]["owner_hints"]["release_manager"].startswith("Treat this run as NO GO")
