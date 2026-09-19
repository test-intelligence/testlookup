"""A CONDITIONAL_GO must say what it is conditional on.

JR-06 / TL-2026-09-19-01-003, measured against the local stack. A run ingested
through `POST /api/v1/ingest/file` (6 passed, 2 failed, 2 skipped, pass rate
75.0%) returned from `GET /api/v1/release-readiness/{run_id}`::

    recommendation    = CONDITIONAL_GO
    conditions_for_go = []
    blocking_issues   = []

`ReleaseGatePage.tsx:457` renders the conditions block behind
`decision.conditions_for_go.length > 0`, so an empty list does not render an
empty section -- it hides the section entirely. A release manager saw
"CONDITIONAL_GO" with no conditions anywhere on the page. The one thing that
verdict exists to communicate was absent from the surface that shows it.

The `reasoning` prose did name the gap ("Run Deep Investigation for richer
insights"), so the information existed; it simply was not in the structured
field the UI reads. This is the sibling of
`test_release_gate_snapshot_explains_verdict.py`: that one made the snapshot
explain the verdict, this one makes the conditions do the same. **No verdict
changes.**

Why CONDITIONAL_GO is reachable at all: the 2026-08-07 sweep recorded that this
path jumped from composite 13 to 60 and never entered the [20,55) conditional
band. That sweep used a throwaway project with no open defects, so `user_impact`
scored ~0. The run measured here sat in a project with 79 open defects, putting
`user_impact` at 100.0 (weight 0.25) and the composite at 35.2 -- inside the
band. So the empty-conditions case is reachable in exactly the situation a
release manager cares about: a real project with a real defect backlog.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from app.services.release_council_service import (  # noqa: E402
    _quick_look_conditions,
)

THRESHOLD = 90.0


class TestANonGoVerdictStatesItsConditions:
    def test_conditional_go_is_never_silent(self):
        # The measured case: 75.0% against a 90.0% threshold.
        conditions = _quick_look_conditions("CONDITIONAL_GO", 75.0, THRESHOLD)
        assert conditions, (
            "a CONDITIONAL_GO with no conditions hides the conditions block in "
            "ReleaseGatePage entirely -- the verdict says 'conditional' and the "
            "page offers nothing to satisfy"
        )

    def test_no_go_states_its_conditions_too(self):
        assert _quick_look_conditions("NO_GO", 40.0, THRESHOLD)

    def test_go_needs_none(self):
        # Nothing is outstanding, so an empty list is correct here, not a gap.
        assert _quick_look_conditions("GO", 100.0, THRESHOLD) == []


class TestTheConditionsAreTrue:
    def test_the_provisional_condition_names_what_was_not_measured(self):
        # The analysis-driven dimensions score 0 on this path because no
        # per-test analyses exist, not because the risks were measured and found
        # absent. Saying so is the difference between "low risk" and "unknown
        # risk" -- absence read as health is how a false GO happens.
        first = _quick_look_conditions("CONDITIONAL_GO", 75.0, THRESHOLD)[0]
        assert "not measured" in first
        assert "Deep Investigation" in first

    def test_the_pass_rate_condition_carries_both_numbers(self):
        conditions = _quick_look_conditions("CONDITIONAL_GO", 75.0, THRESHOLD)
        joined = " ".join(conditions)
        # Actionable means the target AND where it stands now.
        assert "90.0%" in joined
        assert "75.0%" in joined

    def test_no_pass_rate_condition_when_the_pass_rate_is_fine(self):
        # A run at or above the threshold that is still not a GO was held back
        # by something else; inventing a pass-rate condition would misdirect.
        conditions = _quick_look_conditions("CONDITIONAL_GO", 95.0, THRESHOLD)
        joined = " ".join(conditions)
        assert "Raise pass rate" not in joined
        # ...but the provisional condition still stands.
        assert any("not measured" in c for c in conditions)

    def test_the_boundary_is_exclusive(self):
        # Exactly at threshold is not below it.
        at = " ".join(_quick_look_conditions("CONDITIONAL_GO", 90.0, THRESHOLD))
        assert "Raise pass rate" not in at
        just_under = " ".join(_quick_look_conditions("CONDITIONAL_GO", 89.9, THRESHOLD))
        assert "Raise pass rate" in just_under


class TestTheServiceActuallyUsesIt:
    def test_conditions_for_go_is_not_hardcoded_empty(self):
        # The defect was a literal `conditions_for_go=[]` in the response
        # construction. The helper above can be perfect and unused.
        import inspect

        from app.services import release_council_service as svc

        src = inspect.getsource(svc)
        assert "conditions_for_go=[]" not in src, (
            "the quick-look response hardcodes an empty conditions list again"
        )
        assert "conditions_for_go=conditions" in src

    def test_no_escape_sequence_leaked_into_the_text(self):
        # Writing this block via a generator once emitted a literal "\\u2014"
        # instead of an em dash, which would have shipped to the UI verbatim.
        first = _quick_look_conditions("NO_GO", 40.0, THRESHOLD)[0]
        assert "\\u" not in first
        assert "—" in first
