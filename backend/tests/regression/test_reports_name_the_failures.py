"""Regression: AI output must name the suite, the tests, and the reasons.

Owner report (FR-001 / FR-002, 2026-08-16): the `/agents` pipeline has a suite
assigned, but the suite appears nowhere in the LLM output or the generated
reports; and the summaries state failure and skip counts without ever saying
which tests failed or why — "text ... which lack credible details".

That was accurate about the *input*, not the model:

* `ConversationAgent._fetch_run_context` selected run-level aggregates only —
  build, branch, status, counts, pass rate. No per-test rows. No suite.
* `SummaryAgent._build_context` built bullets as `[CATEGORY] conf=N%: <summary>`,
  binding the test id to `_tc_id` and discarding it.
* `AnalysisAgent` had the name and suite in `test_meta` and stored only the
  classifier's result, so nothing downstream could attribute a category to a
  test.

A model cannot name a test it was never shown. The fix supplies the rows; these
guards make sure they keep arriving, because the failure mode is silent — the
prose stays fluent and simply stops being specific.
"""
from __future__ import annotations

import pytest

from app.services.failure_detail_service import (
    effective_suite,
    format_for_prompt,
)


# ── Suite attribution ───────────────────────────────────────────────────────


def test_suite_falls_back_to_the_run_when_the_case_has_none():
    """Old live-stream runs carry the suite only on the run. Reading the case
    alone leaves the suite blank for a whole class of runs — the
    `_effective_suite_sql` rule this repo already learned the hard way."""
    assert effective_suite(None, "AuthenticationSuite") == "AuthenticationSuite"
    assert effective_suite("", "AuthenticationSuite") == "AuthenticationSuite"
    assert effective_suite("   ", "AuthenticationSuite") == "AuthenticationSuite"


def test_the_cases_own_suite_wins_when_present():
    assert effective_suite("CaseSuite", "RunSuite") == "CaseSuite"


def test_no_suite_anywhere_is_none_not_a_placeholder():
    """An invented placeholder would be attributed to a real suite by a reader."""
    assert effective_suite(None, None) is None


# ── The prompt text ─────────────────────────────────────────────────────────


def _detail(**over):
    base = {
        "suite": "GroundTruthSuite",
        "failures": [{
            "test_name": "t_infra_fail",
            "suite": "GroundTruthSuite",
            "status": "BROKEN",
            "error_message": "java.net.ConnectException: Connection refused",
            "failure_category": "INFRASTRUCTURE",
            "confidence_score": 95,
            "root_cause_summary": "Database unreachable.",
            "is_flaky": False,
        }],
        "returned": 1, "total": 1, "truncated": False,
    }
    base.update(over)
    return base


def test_the_prompt_names_the_test_the_suite_and_the_reason():
    """The three things the owner said were missing."""
    text = format_for_prompt(_detail())
    assert "t_infra_fail" in text, "the failing test is not named"
    assert "GroundTruthSuite" in text, "the suite is not stated"
    assert "Connection refused" in text, "the failure reason is not given"


def test_the_classification_carries_its_confidence():
    """INFRASTRUCTURE at 95 and at 30 are different claims."""
    text = format_for_prompt(_detail())
    assert "INFRASTRUCTURE" in text and "95%" in text


def test_an_unanalysed_failure_says_so_rather_than_guessing():
    text = format_for_prompt(_detail(failures=[{
        "test_name": "t_x", "suite": "S", "status": "FAILED",
        "error_message": "boom", "failure_category": None,
        "confidence_score": None, "root_cause_summary": None, "is_flaky": None,
    }]))
    assert "not analysed" in text
    assert "UNKNOWN" not in text, (
        "an unanalysed failure was labelled with a category, which is "
        "indistinguishable from a classifier that ran and could not decide"
    )


def test_a_truncated_list_says_it_is_truncated():
    """A capped list presented as complete is the defect this whole feature
    exists to avoid — a reader would take it as every failure there was."""
    text = format_for_prompt(_detail(returned=25, total=400, truncated=True))
    assert "TRUNCATED" in text.upper()
    assert "400" in text, "the true total is not disclosed"


def test_no_failures_says_none_rather_than_rendering_an_empty_list():
    text = format_for_prompt(_detail(failures=[], returned=0, total=0))
    assert "none recorded" in text.lower()


# ── The wiring: the rows must actually reach the prompts ────────────────────


def test_chat_context_fetches_failure_detail():
    """The chat surface fetched aggregates only, which is why it could not name
    a test. Assert on the CALL, not on prose about it."""
    import ast
    import inspect

    from app.agents.conversation import ConversationAgent

    tree = ast.parse(inspect.getsource(ConversationAgent._fetch_run_context).strip())
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert "failure_detail" in called, (
        "the chat run-context no longer fetches per-test failure detail, so the "
        "model is again being asked to describe failures it has not been shown"
    )
    assert "format_for_prompt" in called


def test_the_summary_bullets_name_the_test():
    """`_tc_id` discarded the identity; the bullet must carry a name."""
    import inspect

    from app.agents.summary_agent import SummaryAgent

    src = inspect.getsource(SummaryAgent._build_context)
    assert 'analysis.get("test_name")' in src, (
        "the summary prompt no longer reads a test name, so its bullets cannot "
        "attribute a category to a test"
    )
    assert 'analysis.get("suite_name")' in src or '"suite_name"' in src


def test_analysis_results_carry_the_test_identity():
    """The name lives in test_meta and used to be dropped when the classifier's
    result was stored. Without this, everything above has nothing to read."""
    import inspect

    from app.agents.analysis_agent import AnalysisAgent

    src = inspect.getsource(AnalysisAgent)
    assert 'setdefault("test_name"' in src, (
        "analysis results no longer carry test_name, so no downstream consumer "
        "can say which test a classification belongs to"
    )
    assert 'setdefault("suite_name"' in src


def test_low_confidence_analyses_are_disclosed_not_silently_dropped():
    """The 50% floor removes bullets. If it removes them all, the prompt used to
    say 'No analyses available' — which reads as 'nothing was analysed' rather
    than 'nothing was confident'.

    Behavioural, because the first version of this guard asserted the phrase
    "confidence floor" appeared in the source — and matched its own explanatory
    comment. The mutation that disabled the disclosure left the comment intact
    and survived.
    """
    from app.agents.summary_agent import SummaryAgent

    context = SummaryAgent()._build_context(
        run_data={"total_tests": 3, "passed_tests": 1, "failed_tests": 2,
                  "skipped_tests": 0, "pass_rate": 33.3, "build_number": "b1"},
        anomaly_summary="",
        anomalies=[],
        analyses={
            "tc-1": {"failure_category": "PRODUCT_BUG", "confidence_score": 20,
                     "root_cause_summary": "maybe", "test_name": "t_low_1"},
            "tc-2": {"failure_category": "INFRASTRUCTURE", "confidence_score": 10,
                     "root_cause_summary": "maybe", "test_name": "t_low_2"},
        },
    )
    assert "omitted" in context and "confidence" in context, (
        "two analysed-but-low-confidence failures produced a context with no "
        "trace of them, so the run is indistinguishable from one where nothing "
        f"was analysed. Context was: {context[:400]!r}"
    )
    assert "2" in context, "the number of omitted analyses is not stated"


@pytest.mark.parametrize("field", ["returned", "total", "truncated"])
def test_the_payload_discloses_its_own_completeness(field):
    from app.services.failure_detail_service import failure_detail  # noqa: F401

    assert field in _detail(), f"{field} missing from the failure-detail contract"
