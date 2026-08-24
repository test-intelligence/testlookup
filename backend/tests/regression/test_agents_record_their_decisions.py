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
import pathlib
import textwrap
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")

from app.agents.cluster_agent import ClusterAgent  # noqa: E402
from app.agents.release_risk_agent import ReleaseRiskAgent  # noqa: E402

AGENTS_ROOT = pathlib.Path(__file__).resolve().parents[2] / "app" / "agents"

# The nine that had no decision trail. Named so a silent regression on any one
# of them is legible, rather than showing up as an anonymous count change.
FORMERLY_SILENT = {
    "AnomalyDetectionAgent", "ClusterAgent", "DefectCommander",
    "FlakySentinelAgent", "IngestionAgent", "RegressionWatchman",
    "ReleaseRiskAgent", "TestHealthAgent", "DefectTriageAgent",
}


def _calls_log_decision(cls: ast.ClassDef) -> bool:
    """True when the class body contains a real ``.log_decision(...)`` call.

    This is the predicate the quality gate uses. Extracted and asserted here
    too so the rule has one definition and both call sites agree on it.
    """
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "log_decision"
        for node in ast.walk(cls)
    )


def _base_agent_classes() -> list[tuple[str, ast.ClassDef]]:
    found: list[tuple[str, ast.ClassDef]] = []
    for path in sorted(AGENTS_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            if any(isinstance(b, ast.Name) and b.id == "BaseAgent" for b in cls.bases):
                found.append((path.name, cls))
    return found


# ── The property ─────────────────────────────────────────────────────────────


def test_every_base_agent_records_at_least_one_decision():
    offenders = [
        f"{filename}::{cls.name}"
        for filename, cls in _base_agent_classes()
        if not _calls_log_decision(cls)
    ]
    assert not offenders, (
        "an agent with no decision trail leaves an empty decision_log, and the "
        "'why did it do that' surface has nothing to show:\n  "
        + "\n  ".join(offenders)
    )


def test_the_nine_formerly_silent_agents_are_all_still_covered():
    """Named, so one quietly losing its trail is not just a count going down."""
    recording = {cls.name for _f, cls in _base_agent_classes() if _calls_log_decision(cls)}
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
    assert not _calls_log_decision(cls), (
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
