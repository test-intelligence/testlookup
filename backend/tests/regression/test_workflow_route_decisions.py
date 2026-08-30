"""The graph's routing decisions — the code that decides which stages run.

``agents/workflow.py`` is 54.6% covered with **381 missed statements**, the
third-largest gap in the backend. Read per function, the shape is familiar: the
pipeline entry points (``run_offline_pipeline``, ``run_deep_pipeline``) and the
lifecycle writers show one executed line each — their bodies have never run.

Those need the whole LangGraph, a DB session and a Mongo client to exercise,
and tests built that way assert the mock. The **routers** do not: they are
plain functions of workflow state that return the name of the next node. They
are also where a silent defect does the most damage, because a wrong answer
does not raise — it just means a stage never runs, and the pipeline reports
success having done less.

The contract that matters most is written in ``_route_after_summary_deep``'s
own docstring: *"Never returns END so all specialist stages are guaranteed to
run."* Its offline twin **does** return END on the same input. Two functions,
nearly identical bodies, opposite answers for the no-triage case — and nothing
checked either.
"""
from __future__ import annotations

import pytest
from langgraph.graph import END

from app.agents.workflow import _route_after_summary, _route_after_summary_deep
from app.core.config import settings

pytestmark = pytest.mark.regression

#: ``AI_CONFIDENCE_THRESHOLD`` is a 0-100 score, not a 0-1 fraction (it is 80).
#: Fixtures must be built relative to it: a "high confidence" analysis written
#: as ``1.0`` sits far BELOW the gate, so a test using it would pass even with
#: the is_flaky exclusion deleted. Caught by mutating that exclusion away.
THRESHOLD = settings.AI_CONFIDENCE_THRESHOLD


def _state(analyses: dict, *, pipeline_run_id: str = "") -> dict:
    """Minimal workflow state. ``pipeline_run_id`` is empty by default so the
    decision emitter short-circuits; the tests that care set it."""
    return {"analyses": analyses, "pipeline_run_id": pipeline_run_id}


def _analysis(confidence: float, *, flaky: bool = False) -> dict:
    return {"confidence_score": confidence, "is_flaky": flaky}


class TestTheOfflineRouter:
    """Skipping triage saves Jira calls and keeps noise out of the tracker."""

    def test_it_ends_when_nothing_meets_the_threshold(self):
        state = _state({"tc-1": _analysis(THRESHOLD - 0.2)})
        assert _route_after_summary(state) == END

    def test_it_ends_when_there_are_no_analyses_at_all(self):
        assert _route_after_summary(_state({})) == END

    def test_it_routes_to_triage_when_one_analysis_qualifies(self):
        state = _state({"tc-1": _analysis(THRESHOLD + 0.1)})
        assert _route_after_summary(state) == "triage"

    def test_one_qualifying_analysis_is_enough(self):
        """The gate is "any", not "all" — a single confident failure is worth a
        ticket even in a run full of low-confidence noise."""
        state = _state({
            "tc-1": _analysis(0.0),
            "tc-2": _analysis(0.0),
            "tc-3": _analysis(THRESHOLD + 0.1),
        })
        assert _route_after_summary(state) == "triage"


class TestTheDeepRouterNeverEnds:
    """``_route_after_summary_deep``'s docstring: "Never returns END so all
    specialist stages are guaranteed to run."

    Returning END here would silently skip the whole
    flaky_sentinel → test_health → release_risk chain. The pipeline would
    finish and report success having produced no flaky analysis, no test-health
    signal and no release risk — the deep pipeline quietly doing the offline
    pipeline's work.
    """

    def test_no_triageable_analyses_jumps_to_the_specialist_chain(self):
        state = _state({"tc-1": _analysis(THRESHOLD - 0.2)})
        assert _route_after_summary_deep(state) == "flaky_sentinel"

    def test_an_empty_analysis_set_still_reaches_the_specialists(self):
        assert _route_after_summary_deep(_state({})) == "flaky_sentinel"

    def test_it_routes_to_triage_when_an_analysis_qualifies(self):
        state = _state({"tc-1": _analysis(THRESHOLD + 0.1)})
        assert _route_after_summary_deep(state) == "triage"

    @pytest.mark.parametrize(
        "analyses",
        [
            {},
            {"tc-1": _analysis(0.0)},
            {"tc-1": _analysis(THRESHOLD + 0.1)},
            {"tc-1": _analysis(THRESHOLD + 0.1, flaky=True)},
            {"tc-1": {}},
        ],
        ids=["empty", "all-low", "qualifying", "flaky-only", "malformed"],
    )
    def test_it_never_returns_end_for_any_input(self, analyses):
        """The invariant itself, swept across every shape the other cases cover
        individually. This is the assertion that fails if someone 'tidies' the
        two routers into one."""
        assert _route_after_summary_deep(_state(analyses)) != END


class TestWhatCountsAsTriageable:
    """Both routers share this filter, so both are checked against it."""

    @pytest.mark.parametrize("router", [_route_after_summary, _route_after_summary_deep])
    def test_a_flaky_analysis_never_triggers_triage(self, router):
        """A flaky test is not a defect to file — filing it is the noise this
        gate exists to prevent, and confidence does not change that."""
        state = _state({"tc-1": _analysis(THRESHOLD + 10, flaky=True)})
        assert router(state) != "triage"

    @pytest.mark.parametrize("router", [_route_after_summary, _route_after_summary_deep])
    def test_confidence_exactly_at_the_threshold_qualifies(self, router):
        """The comparison is `>=`. A run sitting exactly on the configured
        threshold must not fall through the gap."""
        state = _state({"tc-1": _analysis(THRESHOLD)})
        assert router(state) == "triage"

    @pytest.mark.parametrize("router", [_route_after_summary, _route_after_summary_deep])
    def test_a_missing_confidence_score_is_treated_as_zero(self, router):
        """An analysis that never produced a score must not be routed to triage
        by default — absence is not confidence."""
        state = _state({"tc-1": {}})
        assert router(state) != "triage"

    @pytest.mark.parametrize("router", [_route_after_summary, _route_after_summary_deep])
    def test_a_confident_non_flaky_analysis_beside_a_flaky_one_still_triages(self, router):
        state = _state({
            "flaky": _analysis(THRESHOLD + 10, flaky=True),
            "real": _analysis(THRESHOLD + 0.1),
        })
        assert router(state) == "triage"


class TestTheDecisionIsRecorded:
    """LangGraph calls routers synchronously, so the Mongo write cannot be
    awaited. The state mirror is the durable copy that ``_mark_pipeline_done``
    persists to Postgres — if it is lost, the pipeline's own explanation of why
    a stage was skipped is lost with it."""

    def test_a_skip_is_mirrored_into_state_for_durable_persistence(self):
        state = _state({}, pipeline_run_id="run-1")

        _route_after_summary(state)

        decisions = state["_workflow_route_decisions"]
        assert len(decisions) == 1
        assert decisions[0]["decision_point"] == "route_after_summary"
        assert decisions[0]["chosen"] == "end"
        # The rationale is what an operator reads to learn the run was not
        # broken, just unconfident.
        assert "confidence threshold" in decisions[0]["rationale"]
        assert decisions[0]["alternatives"] == ["triage"]

    def test_the_deep_skip_records_the_stage_it_jumped_to(self):
        state = _state({}, pipeline_run_id="run-1")

        _route_after_summary_deep(state)

        decision = state["_workflow_route_decisions"][0]
        assert decision["chosen"] == "flaky_sentinel"
        assert decision["decision_point"] == "route_after_summary_deep"

    def test_recording_survives_having_no_event_loop(self):
        """These tests run synchronously, which is exactly the case the emitter
        documents: "No running loop (e.g. sync test harness)". The background
        Mongo write is skipped; the durable state mirror must not be."""
        state = _state({"tc-1": _analysis(THRESHOLD + 0.1)}, pipeline_run_id="run-1")

        assert _route_after_summary(state) == "triage"
        assert state["_workflow_route_decisions"][0]["chosen"] == "triage"

    def test_nothing_is_recorded_without_a_pipeline_run_id(self):
        """The emitter short-circuits when there is nothing to attribute the
        decision to, rather than writing an orphan record."""
        state = _state({})

        _route_after_summary(state)

        assert "_workflow_route_decisions" not in state

    def test_repeated_routing_appends_rather_than_replaces(self):
        """A run routes at several decision points; each must survive."""
        state = _state({}, pipeline_run_id="run-1")

        _route_after_summary(state)
        _route_after_summary_deep(state)

        assert len(state["_workflow_route_decisions"]) == 2
