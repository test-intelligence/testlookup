"""Regression guard: an agent that made a judgement says why.

The defect
----------
``AgentStageResult.decision_log`` is what answers "why did the agent do that" —
in the UI, in the audit trail, and for anyone asked to justify a release call
after the fact. ``BaseAgent.log_decision`` writes it, and **nine** agents never
called it: ``anomaly_agent``, ``cluster_agent``, ``defect_commander``,
``flaky_sentinel_agent``, ``ingestion_agent``, ``regression_watchman``,
``release_risk_agent``, ``test_health_agent``, ``triage_agent``.

They were tolerated entries in the ``agents.log-decision-present`` baseline, so
the gate was green and correct — the debt was *tolerated, not unseen*. The
branches left unexplained were not cosmetic:

* ``release_risk`` substituted ``CONDITIONAL_GO`` at risk 50 when its scorer
  threw, and the stored decision was indistinguishable from a computed one;
* ``cluster_agent``'s per-test fallback produces output structurally identical
  to real semantic clustering, so nothing downstream could tell them apart;
* ``anomaly_agent``'s regression verdict is a configurable threshold call that
  is unreconstructable from the output;
* ``flaky_sentinel`` and ``test_health`` silently cap their work at 10 and 15
  candidates — a bounded result that does not say it was bounded reads as a
  complete one.

What is guarded
---------------
* **statically**, every ``BaseAgent`` subclass really calls ``log_decision`` —
  by AST, not by substring;
* the predicate rejects a *prose mention* with no call, which is what the old
  substring check could not tell apart;
* behaviourally, the branches above emit a decision naming the reason.
"""
from __future__ import annotations

import ast
import textwrap
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")

from app.agents.cluster_agent import ClusterAgent  # noqa: E402
from app.agents.release_risk_agent import ReleaseRiskAgent  # noqa: E402

from ._agent_graph import (  # noqa: E402
    agent_classes,
    class_calls,
    delegates_to_super_run,
    implementers,
)

# The nine that had no decision trail. Named so a silent regression on any one
# of them is legible, rather than showing up as an anonymous count change.
FORMERLY_SILENT = {
    "AnomalyDetectionAgent", "ClusterAgent", "DefectCommander",
    "FlakySentinelAgent", "IngestionAgent", "RegressionWatchman",
    "ReleaseRiskAgent", "TestHealthAgent", "DefectTriageAgent",
}


def _records_a_decision(cls) -> bool:
    """The predicate the quality gate uses, anchored on ``run``.

    A class that implements ``run`` owes its own trail; one that inherits it,
    or delegates with ``super().run(...)``, is covered where that ``run`` lives.
    """
    return class_calls(cls, "log_decision") or delegates_to_super_run(cls)


# ── The property ─────────────────────────────────────────────────────────────


def test_every_agent_that_implements_run_records_at_least_one_decision():
    offenders = [
        f"{filename}::{cls.name}"
        for filename, cls in implementers()
        if not _records_a_decision(cls)
    ]
    assert not offenders, (
        "an agent with no decision trail leaves an empty decision_log, and the "
        "'why did it do that' surface has nothing to show: "
        + ", ".join(offenders)
    )


def test_the_population_follows_inheritance_not_just_direct_bases():
    """The widening itself, pinned.

    The original check matched ``class X(BaseAgent)`` only. Probing the
    deployment with ``issubclass`` surfaced seven agents it could not see —
    the five hypothesis agents and the two ``_Standalone*`` variants. They were
    all fine (they inherit ``run``), but a subclass that *overrode* ``run``
    without logging would have been invisible.
    """
    names = {cls.name for _f, cls in agent_classes()}
    for indirect in (
        "InfraHypothesisAgent", "CommitHypothesisAgent", "EnvironmentHypothesisAgent",
        "KnownFlakyHypothesisAgent", "RegressionHypothesisAgent",
        "_StandaloneCommander", "_StandaloneWatchman",
    ):
        assert indirect in names, (
            f"{indirect} reaches BaseAgent through a parent and must be in the "
            "population; a direct-base-only scan misses it"
        )
    assert len(names) >= 27, (
        f"only {len(names)} agent classes found — the graph has lost members and "
        "the properties above would pass vacuously"
    )


def test_the_nine_formerly_silent_agents_are_all_still_covered():
    """Named, so one quietly losing its trail is not just a count going down."""
    recording = {
        cls.name for _f, cls in agent_classes() if _records_a_decision(cls)
    }
    missing = sorted(FORMERLY_SILENT - recording)
    assert not missing, f"these had their decision trail restored and lost it again: {missing}"


def test_a_prose_mention_is_not_a_decision_trail():
    """Guards the guard: the failure the substring check could not see.

    ``"log_decision" in source`` was satisfied by a docstring, a comment, or a
    TODO — so a file could *describe* the trail it never writes and still pass.
    """
    prose_only = ast.parse(textwrap.dedent('''
        class ProseAgent(BaseAgent):
            """This agent intends to call self.log_decision(...) at each branch."""

            async def run(self, state):
                # TODO: await self.log_decision(...) once the branches settle
                return {}
    '''))
    cls = next(n for n in ast.walk(prose_only) if isinstance(n, ast.ClassDef))

    assert "log_decision" in ast.unparse(cls), "the mention must be present"
    assert not _records_a_decision(cls), (
        "a docstring and a TODO are not a decision trail — this is exactly what "
        "the old substring check accepted"
    )


# ── The branches that were silent ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clustering_says_when_it_fell_back_to_per_test(monkeypatch):
    """Semantic and per-test fallback produce identical shapes downstream."""
    decisions: list[dict] = []

    async def _capture(_self, _run_id, **kwargs):
        decisions.append(kwargs)

    monkeypatch.setattr(ClusterAgent, "mark_stage_running", AsyncMock())
    monkeypatch.setattr(ClusterAgent, "mark_stage_done", AsyncMock())
    monkeypatch.setattr(ClusterAgent, "broadcast_progress", AsyncMock())
    monkeypatch.setattr(ClusterAgent, "log_decision", _capture)

    await ClusterAgent().run({
        "pipeline_run_id": "pipe-1",
        "project_id": "proj-1",
        "failed_test_ids": [],
    })

    points = {d["decision_point"] for d in decisions}
    assert "clustering_scope" in points, (
        "a run with no failures records nothing about why it clustered nothing"
    )
    scope = next(d for d in decisions if d["decision_point"] == "clustering_scope")
    assert scope["chosen"] == "skip_no_failures"


@pytest.mark.asyncio
async def test_release_risk_says_when_its_verdict_was_substituted(monkeypatch):
    """A fallback CONDITIONAL_GO must not look like a computed one.

    This stage gates releases; a substituted verdict at risk 50 is stored in
    exactly the same shape as a scored one, so the substitution is only ever
    visible if the agent says so.
    """
    decisions: list[dict] = []

    async def _capture(_self, _run_id, **kwargs):
        decisions.append(kwargs)

    monkeypatch.setattr(ReleaseRiskAgent, "mark_stage_running", AsyncMock())
    monkeypatch.setattr(ReleaseRiskAgent, "mark_stage_done", AsyncMock())
    monkeypatch.setattr(ReleaseRiskAgent, "broadcast_progress", AsyncMock())
    monkeypatch.setattr(ReleaseRiskAgent, "log_decision", _capture)
    monkeypatch.setattr(ReleaseRiskAgent, "_assemble_input_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr(ReleaseRiskAgent, "_persist_decision", AsyncMock())
    monkeypatch.setattr(
        ReleaseRiskAgent, "_evaluate",
        AsyncMock(side_effect=RuntimeError("scorer exploded")),
    )

    await ReleaseRiskAgent().run({
        "pipeline_run_id": "pipe-1",
        "project_id": "proj-1",
        "test_run_id": "run-1",
    })

    scoring = [d for d in decisions if d["decision_point"] == "release_scoring"]
    assert scoring, "the substitution left no trace"
    assert scoring[0]["chosen"] == "fallback_conditional_go"
    assert "manual review" in scoring[0]["rationale"].lower()

    verdict = [d for d in decisions if d["decision_point"] == "release_recommendation"]
    assert verdict, "the terminal GO/NO_GO judgement was not recorded"
    assert verdict[0]["chosen"] == "CONDITIONAL_GO"


def test_the_guard_and_the_gate_agree_on_who_is_covered():
    """One rule, two call sites — assert they reach the same conclusion.

    The gate blocks CI and this suite blocks the branch; if they compute the
    population differently, one of them is quietly guarding a smaller set. That
    failure mode has bitten this repo before, which is why the predicate lives
    in a single module and is pinned against the gate here rather than trusted
    to stay in sync by convention.
    """
    import importlib.util
    import pathlib
    import sys

    gate_path = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "quality_gate.py"
    spec = importlib.util.spec_from_file_location("_qg_for_test", gate_path)
    assert spec and spec.loader, f"could not load the quality gate at {gate_path}"
    gate = importlib.util.module_from_spec(spec)
    # Register before exec: the gate defines dataclasses, and @dataclass looks
    # its own module up in sys.modules while building __init__.
    sys.modules["_qg_for_test"] = gate
    try:
        spec.loader.exec_module(gate)
    finally:
        sys.modules.pop("_qg_for_test", None)

    gate_pop = {
        cls.name for _p, cls in gate._agent_class_graph()
        if gate._defines_run(cls) and not gate._delegates_to_super_run(cls)
    }
    test_pop = {cls.name for _f, cls in implementers()}

    assert gate_pop == test_pop, (
        "the gate and this suite disagree about which agents owe a decision "
        f"trail; only in gate: {sorted(gate_pop - test_pop)}, "
        f"only in tests: {sorted(test_pop - gate_pop)}"
    )
    assert not gate._agents_log_decision_present(), (
        "the gate reports violations this suite did not"
    )
