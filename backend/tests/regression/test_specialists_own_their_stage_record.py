"""Regression guard: a stage that runs records that it ran.

The defect
----------
``BaseAgent`` provides the whole observability contract — the
``stage_started`` / ``stage_completed`` events, the OTEL span, the Prometheus
histograms, the decision trail flushed onto ``AgentStageResult`` — but
``BaseAgent.run`` is abstract, so **calling** that lifecycle is opt-in per
subclass. Two specialists were not subclasses at all:

===========================  ==================  ====================
stage                        ``BaseAgent``?      lifecycle called?
===========================  ==================  ====================
``regression_watchman``      yes                 yes
``change_ownership``         yes                 **no**
``contract_validation``      **no**              no
``log_intelligence``         **no**              no
===========================  ==================  ====================

Measured on the deployment 2026-08-24, over 6 deep runs, in
``pipeline_event_log``::

    regression_watchman | stage_started   | 6
    regression_watchman | stage_completed | 6
    change_ownership    | decision_made   | 6      <- and nothing else
    contract_validation | (no events at all)
    log_intelligence    | (no events at all)

Two stages produced findings that reached the report while being entirely
absent from the pipeline timeline. #840 backfilled the ``AgentStageResult``
row from the node wrapper, which fixed the *row* — deliberately status and
timestamps only — but a backfilled row carries no span, no metrics, no
decision trail, and no timeline entry.

What is guarded
---------------
* the two specialists are ``BaseAgent`` subclasses carrying their stage name;
* **statically**, every ``BaseAgent`` subclass drives its own lifecycle — the
  property behind the table above, so a sixteenth agent is covered too;
* behaviourally, the paths that previously skipped it: the flag-on specialist
  run, and ``change_ownership``'s early return.

The static check is the load-bearing one. Asserting on the four known stages
would have passed happily on the day a fifth was added.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")

from app.agents.base import BaseAgent  # noqa: E402
from app.agents.change_ownership_agent import ChangeOwnershipAgent  # noqa: E402
from app.agents.contract_agent import ContractAgent  # noqa: E402
from app.agents.log_intelligence_agent import LogIntelligenceAgent  # noqa: E402

from ._agent_graph import agent_classes, class_calls, implementers  # noqa: E402

LIFECYCLE = ("mark_stage_running", "mark_stage_done")


# ── The two that were not subclasses at all ──────────────────────────────────


@pytest.mark.parametrize("agent,stage", [
    (ContractAgent, "contract_validation"),
    (LogIntelligenceAgent, "log_intelligence"),
])
def test_the_specialist_is_a_base_agent(agent, stage):
    assert issubclass(agent, BaseAgent), (
        f"{agent.__name__} skips the whole observability contract"
    )
    assert agent.stage_name == stage, (
        "the stage name is the key the row and the events are written under"
    )


# ── The property, so a sixteenth agent is covered ────────────────────────────


def test_every_agent_that_implements_run_drives_its_own_stage_lifecycle():
    """``run`` is abstract, so the lifecycle is opt-in — assert every opt-in.

    Anchored on ``run`` and resolved through the inheritance graph, the same
    population the decision-trail guard uses: the class that implements the
    stage owes the record, and one that inherits ``run`` is covered where that
    ``run`` is defined.
    """
    offenders = []
    for filename, cls in implementers():
        missing = [name for name in LIFECYCLE if not class_calls(cls, name)]
        if missing:
            offenders.append(f"{filename}::{cls.name} missing {', '.join(missing)}")

    assert not offenders, (
        "a stage that never marks itself leaves no span, no metrics and no "
        "timeline entry — the row alone is a backfill, not a record: "
        + "; ".join(offenders)
    )


def test_the_lifecycle_guard_can_actually_see_the_agents():
    """A guard that finds nothing to check reports green for the wrong reason."""
    classes = agent_classes()
    assert len(classes) >= 27, (
        f"only {len(classes)} agent classes found — the scan has lost its "
        "population and would pass vacuously"
    )
    assert len(implementers()) >= 20, (
        f"only {len(implementers())} implement run(); the property above would "
        "cover almost nothing"
    )
    names = {cls.name for _f, cls in classes}
    for required in ("ContractAgent", "LogIntelligenceAgent", "ChangeOwnershipAgent"):
        assert required in names, f"{required} dropped out of the guarded set"


# ── The paths that used to skip it ───────────────────────────────────────────


@pytest.fixture
def lifecycle_spies(monkeypatch):
    running, done = AsyncMock(), AsyncMock()
    for agent in (ContractAgent, LogIntelligenceAgent, ChangeOwnershipAgent):
        monkeypatch.setattr(agent, "mark_stage_running", running)
        monkeypatch.setattr(agent, "mark_stage_done", done)
        monkeypatch.setattr(agent, "log_decision", AsyncMock())
    return running, done


@pytest.mark.asyncio
async def test_contract_validation_records_its_stage(monkeypatch, lifecycle_spies):
    """The flag-on path: previously produced findings and no lifecycle event."""
    running, done = lifecycle_spies
    monkeypatch.setattr(
        ContractAgent, "validate_cluster",
        AsyncMock(return_value={"status": "complete", "evidence_refs": [{"kind": "x"}]}),
    )

    delta = await ContractAgent().run({"pipeline_run_id": "pipe-1", "failed_test_ids": ["t1"]})

    assert running.await_count == 1
    assert done.await_count == 1
    assert done.await_args.kwargs["confidence_score"] == 80
    assert done.await_args.kwargs["evidence_count"] == 1
    assert delta["completed_stages"] == ["contract_validation"]


@pytest.mark.asyncio
async def test_change_ownership_records_its_stage_on_an_early_return(lifecycle_spies):
    """Its eight return paths funnel through ``_result``; the lifecycle wraps them.

    This is the path that made the defect invisible: with no project scope the
    agent returned before doing any work, so a lifecycle driven from inside the
    body would have been skipped exactly when the stage still reported findings.
    """
    running, done = lifecycle_spies

    delta = await ChangeOwnershipAgent().run({"pipeline_run_id": "pipe-1"})

    assert delta["status"] == "not_enough_evidence"
    assert running.await_count == 1, "the stage ran but never marked itself running"
    assert done.await_count == 1, "the stage returned but never marked itself done"
    assert done.await_args.kwargs["fallback_reason"], (
        "a degraded stage has to name why, or it reads as a stage that did nothing"
    )
