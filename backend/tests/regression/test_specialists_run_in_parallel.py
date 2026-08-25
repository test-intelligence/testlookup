"""Regression guard: the read-only specialists fan out, at equal depth (F-9).

The finding
-----------
Four independent read-only specialists ran in a strict chain::

    cluster_investigation_join -> contract_validation -> log_intelligence
                               -> regression_watchman -> change_ownership

so a deep run paid the **sum** of their latencies instead of the slowest one.
Their declared budgets alone are 15s + 20s + 10s plus the watchman.

Why it was serial, and why that reason does not forbid fan-out
--------------------------------------------------------------
An earlier parallel attempt double-executed the chain. The mechanism is
recorded in the summary fan-in note in ``workflow.py``: LangGraph schedules by
**superstep**, so a join whose predecessors sit at *different depths* is reached
once by the shallow branch and again by the deep one — running it, and
everything after it, twice.

The lesson is not "do not fan out". It is **keep every predecessor at the same
depth**. All four specialists are now direct successors of
``cluster_investigation_join``, so ``gap_detection`` has four predecessors at
one depth and fires exactly once.

Why these four are safe to run concurrently
-------------------------------------------
* their outputs are **disjoint** — ``contract_findings``, ``log_findings``,
  ``regression_classification``, ``change_ownership_findings``;
* none reads another's output. (``change_ownership_agent`` mentions
  ``regression_classification``, but reads it from its own ``baseline_diff``
  dict, **not** from workflow state — checked, because a naive grep suggests a
  dependency that is not there);
* every field they *share* already carries a reducer, so concurrent writes
  merge rather than collide.

What is guarded
---------------
* the chain is gone and the fan-out exists;
* every specialist has the same single predecessor — the equal-depth invariant
  that prevents the double-execution;
* the join has exactly those four predecessors;
* the outputs stay disjoint, so no reducer is required for them.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("asyncpg")

from app.agents.state import WorkflowState  # noqa: E402

_SPECIALISTS = (
    "contract_validation",
    "log_intelligence",
    "regression_watchman",
    "change_ownership",
    # Added when DefectCommander was wired into the deep pipeline behind a
    # default-off flag. It belongs INSIDE this guard, not beside it: the
    # equal-depth invariant is exactly what a fifth fan-out member could break,
    # and it is the one member that mutates.
    "defect_commander",
)
_JOIN = "cluster_investigation_join"


def _edges():
    """The REAL compiled graph, not its source text.

    An earlier fix in this repo shipped green because its test read
    ``inspect.getsource`` and never executed the code -- a source assertion
    cannot see behaviour. Graph topology is behaviour, so assert on the graph.
    """
    from app.agents.workflow import _build_deep_graph

    return sorted((a, b) for a, b in _build_deep_graph().edges)


def _predecessors(node: str):
    return sorted(a for a, b in _edges() if b == node)


# ── The serial chain is gone ─────────────────────────────────────────────────


def test_no_specialist_feeds_another():
    """The chain made a run pay the sum of latencies instead of the max."""
    chain = [(a, b) for a, b in _edges() if a in _SPECIALISTS and b in _SPECIALISTS]

    assert chain == [], f"still serialised: {chain}"


# ── They fan out from one node, at one depth ─────────────────────────────────


@pytest.mark.parametrize("specialist", _SPECIALISTS)
def test_every_specialist_has_the_same_single_predecessor(specialist):
    """The equal-depth invariant. Predecessors at different depths are what
    made the earlier attempt fire the join -- and everything after it -- twice."""
    assert _predecessors(specialist) == [_JOIN], (
        f"{specialist} sits at a different depth; the join would double-fire"
    )


def test_the_join_waits_for_exactly_those_specialists():
    """No more and no fewer — an extra predecessor at another depth double-fires."""
    assert _predecessors("gap_detection") == sorted(_SPECIALISTS)


# ── Concurrency safety ───────────────────────────────────────────────────────


def test_the_specialist_outputs_are_disjoint():
    """Two parallel nodes writing one un-reduced key is the collision case.
    Each writes a different key, so no reducer is needed for them."""
    outputs = {
        "contract_validation": "contract_findings",
        "log_intelligence": "log_findings",
        "regression_watchman": "regression_classification",
        "change_ownership": "change_ownership_findings",
        "defect_commander": "defect_promotion",
    }

    assert set(outputs) == set(_SPECIALISTS), (
        "a specialist without an output key here is unchecked for collisions"
    )
    assert len(set(outputs.values())) == len(outputs), "outputs must not overlap"


@pytest.mark.parametrize("field", [
    "completed_stages", "skipped_stages", "errors", "current_stage", "agent_contracts",
])
def test_every_shared_field_has_a_reducer(field):
    """These ARE written by all four concurrently, so they must merge."""
    from typing import get_type_hints

    hints = get_type_hints(WorkflowState, include_extras=True)
    annotation = hints[field]

    assert getattr(annotation, "__metadata__", None), (
        f"{field} is written by every specialist and has no reducer — "
        "concurrent writes would collide"
    )


def test_change_ownership_does_not_read_the_watchman_from_state():
    """It mentions regression_classification, but from its own baseline_diff.
    If that ever changes, the fan-out becomes a race and this must fail."""
    from app.agents import change_ownership_agent

    src = inspect.getsource(change_ownership_agent)

    assert 'state.get("regression_classification")' not in src
    assert 'state["regression_classification"]' not in src
