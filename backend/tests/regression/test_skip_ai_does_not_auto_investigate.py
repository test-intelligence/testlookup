"""A run uploaded with "Skip AI analysis" never starts the automatic Investigator (D-0006).

GPT-6 (exploratory test, 2026-11-14) uploaded nine reports with **Skip AI
analysis** ticked (``run_ai=false``). Each newly failing test still started an
automatic Shadow investigation (``triggered_by=auto:newly_failing``): 54 paid
model calls and 9 of the project's 10 daily investigation slots. ``run_ai``
gated only the suite comparison and the agent pipeline; the transition
notification task, staged for every run with only the run id, called
``maybe_auto_trigger`` regardless.

Now the flag rides the ``transition_notifications`` outbox payload when false,
the task passes it on, and ``evaluate_run_transitions(auto_investigate=False)``
keeps every notification and the state stamp but skips the auto-trigger.

These drive the real ``evaluate_run_transitions`` (the real transition
detection) with the database session and the delivery step replaced, and a spy
for ``maybe_auto_trigger``. No model is involved: the spy stands where the
investigation would be enqueued.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.services import notification_transitions as transitions

pytestmark = pytest.mark.regression

RUN = uuid.UUID("36a236ee-0802-4c50-9f90-1c14bfa14bd0")
PROJECT = uuid.UUID("c661192d-0000-4000-8000-000000000001")


def _result(**methods):
    return SimpleNamespace(**{name: Mock(return_value=value) for name, value in methods.items()})


@pytest.fixture
def world(monkeypatch):
    """One run, one test that failed after passing: a newly failing transition."""
    run = SimpleNamespace(id=RUN, project_id=PROJECT, build_number="2")
    project = SimpleNamespace(name="GPT6 Exploratory 2026-11", component_owner_map=None)
    case = SimpleNamespace(
        id=uuid.uuid4(), test_fingerprint="fp", status="FAILED",
        test_name="synthetic_total_includes_delivery", suite_name="GPT6 W3 synthetic",
    )
    # Passing before, one failure already counted: this failure crosses the threshold of 2.
    state = SimpleNamespace(
        last_run_id=uuid.uuid4(), state="passing", consecutive_failures=1,
        is_known_flaky=False, last_notified_state="passing",
    )
    enabled = {"transitions": True}

    session = MagicMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None

    def _execute_results():
        yield _result(scalar_one_or_none=run)
        yield _result(scalar_one_or_none=project)
        yield _result(all=[case])
        while True:  # evidence and cluster reads, should the policy enable events
            yield _result(all=[], scalars=Mock(return_value=_result(all=[])))

    results = _execute_results()
    session.execute = AsyncMock(side_effect=lambda *_a, **_k: next(results))
    session.commit = AsyncMock()

    async def _policy(_db, _project_id):
        # No enabled events: delivery is a no-op; detection stays real.
        return SimpleNamespace(
            transitions_enabled=enabled["transitions"],
            consecutive_failure_threshold=2,
            enabled_events=[],
        )

    delivered = AsyncMock(return_value={"staged": 0})
    auto_trigger = AsyncMock(return_value=None)

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", Mock(return_value=session))
    monkeypatch.setattr(transitions, "get_effective_policy", _policy)
    monkeypatch.setattr(transitions, "_ensure_transition_order", AsyncMock())
    monkeypatch.setattr(transitions, "_load_or_seed_states", AsyncMock(return_value={"fp": state}))
    monkeypatch.setattr(transitions, "_dispatch_transition_events", delivered)
    monkeypatch.setattr(
        "app.services.github_pr_comment_service._flaky_fingerprints", AsyncMock(return_value=set())
    )
    monkeypatch.setattr("app.services.agent_investigation_service.maybe_auto_trigger", auto_trigger)
    return SimpleNamespace(auto_trigger=auto_trigger, delivered=delivered, session=session, enabled=enabled)


@pytest.mark.asyncio
async def test_a_skip_ai_run_keeps_its_notifications_but_starts_no_investigation(world):
    await transitions.evaluate_run_transitions(RUN, auto_investigate=False)
    world.auto_trigger.assert_not_awaited()
    # The transition evaluation itself still ran to the end: delivery staged, state committed.
    world.delivered.assert_awaited_once()
    world.session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_an_ai_on_run_still_starts_the_automatic_investigation(world):
    await transitions.evaluate_run_transitions(RUN, auto_investigate=True)
    world.auto_trigger.assert_awaited_once_with(RUN, "auto:newly_failing")


@pytest.mark.asyncio
async def test_the_default_is_ai_on(world):
    """Callers that pass nothing (re-finalization, older payloads) keep today's behaviour."""
    await transitions.evaluate_run_transitions(RUN)
    world.auto_trigger.assert_awaited_once_with(RUN, "auto:newly_failing")


@pytest.mark.asyncio
@pytest.mark.parametrize("auto_investigate", [True, False])
async def test_disabled_transitions_never_investigate(world, auto_investigate):
    world.enabled["transitions"] = False
    result = await transitions.evaluate_run_transitions(RUN, auto_investigate=auto_investigate)
    assert result == {"skipped": "transitions_disabled"}
    world.auto_trigger.assert_not_awaited()
