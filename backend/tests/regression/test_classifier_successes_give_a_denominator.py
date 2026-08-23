"""Regression guard: the fast classifier has a denominator.

The gap
-------
#802 made ``FastClassifier`` report *why* it returned nothing, and the outcome
was logged as a pipeline decision. But the carrier dropped successes::

    if classifier_outcome not in ("classified", "not_attempted"):
        analysis["_classifier_outcome"] = classifier_outcome

So only failures and abstentions were ever recorded. With no successes there is
no denominator, and **zero entries reads identically whether the classifier ran
perfectly or never ran at all.**

That was not hypothetical. Measured on the homelab 2026-08-23 against 40
deliberately novel failures (novel so the content-addressed analysis cache
would miss): the decision log held **zero** classifier entries. Only the Mongo
payload's ``classified_by: fast_classifier`` proved the classifier had in fact
run 40 times and succeeded 40 times.

A metric that looks healthiest when nothing happened is the same fail-open
shape the instrumentation was added to remove.

What is guarded
---------------
* a successful classification is carried and recorded, so attempts are countable;
* ``not_attempted`` is still dropped — it is not a classifier call, and one
  entry per test case would bloat the decision log on every rules/ML run;
* a parse failure still lands on the parse-failure decision point, so the
  harness's existing count does not change;
* an abstention counts as an attempt but **not** as a failure;
* the harness reports ``measured: false`` when nothing ran, rather than a
  reassuring 0% failure rate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("asyncpg")

_BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "pipeline"
sys.path.insert(0, str(_BENCH))

from aggregate import summarize_classifier  # noqa: E402

from app.services import agent as agent_mod  # noqa: E402


def _stage(*outcomes: str) -> dict:
    """A stage whose decision log carries the given classifier outcomes."""
    return {
        "decision_log": [
            {
                "decision_point": (
                    "classifier_schema_validation"
                    if o == "parse_failed"
                    else "classifier_call_outcome"
                ),
                "chosen": o,
            }
            for o in outcomes
        ]
    }


# ── The carrier records successes ────────────────────────────────────────────
#
# These call run_triage_agent for real. An earlier version of this file checked
# `inspect.getsource` instead -- and passed while the behaviour was broken,
# because the fast-classifier success path `return quick` short-circuits BEFORE
# the assignment the source test was reading. A source test cannot see an early
# return. Never assert on source text for a behavioural claim.


class _Quick:
    """A confident fast-classifier verdict."""

    @staticmethod
    async def classify_with_outcome(**_kw):
        return {
            "root_cause_summary": "connection refused",
            "failure_category": "INFRASTRUCTURE",
            "confidence_score": 95,
            "recommended_actions": [],
            "classified_by": "fast_classifier",
        }, "classified"


class _Abstains:
    @staticmethod
    async def classify_with_outcome(**_kw):
        return None, "low_confidence"


@pytest.fixture
def _quiet(monkeypatch):
    """Silence the side effects run_triage_agent performs around the verdict."""
    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(agent_mod, "_store_audit_trail", _noop)
    monkeypatch.setattr(agent_mod, "_store_analysis_cache", _noop)


@pytest.mark.asyncio
async def test_a_successful_classification_is_carried(monkeypatch, _quiet):
    """The regression: this path returns early, so it needs its own assignment."""
    import app.services.training.classifier as classifier_mod

    monkeypatch.setattr(classifier_mod, "FastClassifier", _Quick)

    analysis = await agent_mod.run_triage_agent(
        test_name="test_payments_timeout",
        error_message="ConnectionRefusedError: [Errno 111] Connection refused",
        stack_trace="",
        test_case_id="tc-1",
        project_id="p-1",
    )

    assert analysis.get("classified_by") == "fast_classifier", "fast path did not run"
    assert analysis.get("_classifier_outcome") == "classified", (
        "a success on the fast path must be recorded, or there is no denominator"
    )


@pytest.mark.asyncio
async def test_an_abstention_does_not_short_circuit_into_a_success(monkeypatch, _quiet):
    """low_confidence falls through to ReAct; it must not be logged as classified."""
    import app.services.training.classifier as classifier_mod

    monkeypatch.setattr(classifier_mod, "FastClassifier", _Abstains)

    async def _boom(*_a, **_kw):
        raise RuntimeError("react path reached, as expected")

    monkeypatch.setattr(agent_mod, "_build_agent_executor", _boom, raising=False)

    try:
        analysis = await agent_mod.run_triage_agent(
            test_name="t", error_message="odd failure", stack_trace="",
            test_case_id="tc-2", project_id="p-1",
        )
    except Exception:
        return  # reaching ReAct at all is the assertion

    assert analysis.get("_classifier_outcome") != "classified"


# ── The rate is computed over attempts ───────────────────────────────────────


def test_attempts_count_every_outcome():
    summary = summarize_classifier([
        _stage("classified", "classified", "parse_failed", "low_confidence")
    ])

    assert summary["attempts"] == 4
    assert summary["outcomes"] == {
        "classified": 2, "low_confidence": 1, "parse_failed": 1,
    }


def test_an_abstention_is_an_attempt_but_not_a_failure():
    summary = summarize_classifier([_stage("classified", "low_confidence")])

    assert summary["attempts"] == 2
    assert summary["failures"] == 0
    assert summary["failure_rate"] == 0.0
    assert summary["abstention_rate"] == 0.5


def test_parse_and_call_failures_both_count_as_failures():
    summary = summarize_classifier([
        _stage("classified", "classified", "parse_failed", "call_failed")
    ])

    assert summary["failures"] == 2
    assert summary["failure_rate"] == 0.5


def test_the_measured_run_reproduces_as_zero_of_forty():
    """The homelab reading: 40 novel failures, all classified."""
    summary = summarize_classifier([_stage(*(["classified"] * 40))])

    assert summary["attempts"] == 40
    assert summary["failures"] == 0
    assert summary["failure_rate"] == 0.0
    assert summary["measured"] is True


# ── Absence is not health ────────────────────────────────────────────────────


def test_a_classifier_that_never_ran_is_not_reported_as_healthy():
    """The whole point: 0 failures over 0 attempts is not a 0% failure rate."""
    summary = summarize_classifier([{"decision_log": []}])

    assert summary["attempts"] == 0
    assert summary["measured"] is False
    assert summary["failure_rate"] is None, (
        "a rate of 0.0 here would read as 'never fails' when nothing ran"
    )


def test_unrelated_decision_points_are_ignored():
    summary = summarize_classifier([{
        "decision_log": [
            {"decision_point": "route_analysis_mode", "chosen": "llm"},
            {"decision_point": "summary_schema_validation", "chosen": "failed"},
        ]
    }])

    assert summary["attempts"] == 0


def test_a_small_sample_is_flagged():
    summary = summarize_classifier([_stage("classified", "classified")])

    assert summary["sufficient_samples"] is False
